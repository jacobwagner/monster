from chef import Node, Client
from provisioner import Provisioner
from gevent import spawn, joinall, sleep

from monster import util
from monster.clients.openstack import Creds, Clients
from monster.server_helper import run_cmd


class Openstack(Provisioner):
    """
    Provisions chef nodes in openstack vms
    """
    def __init__(self):
        self.names = []
        self.name_index = {}
        self.creds = Creds()
        self.keystone_client = Clients(self.creds).get_client("keystoneclient")
        self.nova_client = Clients(self.creds).get_client("novaclient")

    def name(self, name, deployment, number=None):
        """
        Helper for naming nodes
        :param name: name for node
        :type name: String
        :param deployment: deployment object
        :type deployment: Deployment
        :param number: number to append to name
        :type number: int
        :rtype: string
        """
        if name in self.name_index:
            # Name already exists, use index to name
            num = self.name_index[name] + 1
            self.name_index[name] = num
            return "{0}-{1}{2}".format(deployment.name, name, num)

        # Name doesn't exist initalize index use name
        self.name_index[name] = 1
        return "{0}-{1}".format(deployment.name, name)

    def provision(self, template, deployment):
        """
        Provisions a ChefNode using OpenStack
        :param template: template for cluster
        :type template: dict
        :param deployment: ChefDeployment to provision for
        :type deployment: ChefDeployment
        :rtype: list
        """
        util.logger.info("Provisioning in the cloud!")
        # acquire connection

        # create instances concurrently
        events = []
        for features in template['nodes']:
            name = self.name(features[0], deployment)
            self.names.append(name)
            flavor = "2GBP"
            if "compute" in name:
                flavor = "8GBP"
            events.append(spawn(self.chef_instance, deployment, name,
                                flavor=flavor))
        joinall(events)

        # acquire chef nodes
        chef_nodes = [event.value for event in events]
        return chef_nodes

    def destroy_node(self, node):
        """
        Destroys chef node from openstack
        :param node: node to destroy
        :type node: ChefNode
        """
        cnode = Node(node.name, node.environment.local_api)
        if cnode.exists:
            self.nova_client.servers.get(node['uuid']).delete()
            cnode.delete()
        client = Client(node.name, node.environment.local_api)
        if client.exists:
            client.delete()

    def chef_instance(self, deployment, name, flavor="2GBP"):
        """
        Builds an instance with desired specs and inits it with chef
        :param client: compute client object
        :type client: novaclient.client.Client
        :param deployment: deployement to add to
        :type deployment: ChefDeployment
        :param name: name for instance
        :type name: string
        :param flavor: desired flavor for node
        :type flavor: string
        :rtype: ChefNode
        """
        image = deployment.os_name
        server, password = self.build_instance(name=name, image=image,
                                               flavor=flavor)
        run_list = ",".join(util.config[str(self)]['run_list'])
        run_list_arg = ""
        if run_list:
            run_list_arg = "-r {0}".format(run_list)
        command = 'knife bootstrap {0} -u root -P {1} -N {2} {3}'.format(
            server.accessIPv4, password, name, run_list_arg)
        run_cmd(command)
        node = Node(name, api=deployment.environment.local_api)
        node.chef_environment = deployment.environment.name
        node['in_use'] = "provisioning"
        node['ipaddress'] = server.accessIPv4
        node['password'] = password
        node['uuid'] = server.id
        node['current_user'] = "root"
        node.save()
        return node

    def build_instance(self, name="server", image="precise",
                       flavor="2GBP"):
        """
        Builds an instance with desired specs
        :param client: compute client object
        :type client: novaclient.client.Client
        :param name: name of server
        :type name: string
        :param image: desired image for server
        :type image: string
        :param flavor: desired flavor for server
        :type flavor: string
        :rtype: Server
        """
        config = util.config[str(self)]

        # get flavor
        try:
            flavor_name = config['flavors'][flavor]
        except KeyError:
            raise Exception("Flavor not supported:{0}".format(flavor))
        flavor_obj = self._client_search(self.nova_client.flavors.list, "name",
                                         flavor_name, attempts=10)

        # get image
        try:
            image_name = config['images'][image]
        except KeyError:
            raise Exception("Image not supported:{0}".format(image))
        image_obj = self._client_search(self.nova_client.images.list, "name",
                                        image_name, attempts=10)

        # gather networks
        desired_networks = util.config[str(self)]['networks']
        networks = []
        for network in desired_networks:
            obj = self._client_search(self.neutron.list, "label",
                                      network, attempts=10)
            networks.append({"net-id": obj.id})

        # build instance
        server = self.nova_client.servers.create(name, image_obj.id,
                                                 flavor_obj.id, nics=networks)
        password = server.adminPass
        util.logger.info("Building:{0}".format(name))
        server = self.wait_for_state(self.nova_client.servers.get, server,
                                     "status", ["ACTIVE", "ERROR"])
        if server.status == "ERROR":
            util.logger.error("Instance entered error state. Retrying...")
            server.delete()
            return self.build_instance(name=name, image=image, flavor=flavor)

        return (server, password)

    def _client_search(self, collection_fun, attr, desired, attempts=None,
                       interval=1):
        """
        Searches for a desired attribute in a list of objects
        :param collection_fun: function to get list of objects
        :type collection_fun: function
        :param attr: attribute of object to check
        :type attr: string
        :param desired: desired value of object's attribute
        :type desired: object
        :param attempts: number of attempts to acheive state
        :type attempts: int
        :param interval: time between attempts
        :type interval: int
        :rtype: object
        """
        obj_collection = None
        attempt = 0
        in_attempt = lambda x: not attempts or attempts > x
        while in_attempt(attempt):
            try:
                obj_collection = collection_fun()
                break
            except Exception as e:
                util.logger.error("Wait: Request error:{0}-{1}".
                                  format(desired, e))
                continue
        get_attr = lambda x: getattr(x, attr)
        util.logger.debug("Search:{0} for {1} in {2}".format(
            attr, desired, ",".join(map(get_attr, obj_collection))))
        for obj in obj_collection:
            if getattr(obj, attr) == desired:
                return obj
        raise Exception("Client search fail:{0} not found".format(desired))

    def wait_for_state(self, fun, obj, attr, desired, interval=15,
                       attempts=None):
        """
        Waits for a desired state of an object using gevented sleep
        :param fun: function to update object
        :type fun: function
        :param obj: object which to check state
        :type obj: obj
        :param attr: attribute of object of which state resides
        :type attr: str
        :param desired: desired state of attribute
        :type desired: string
        :param interval: interval to check state in secs
        :param interval: int
        :param attempts: number of attempts to acheive state
        :type attempts: int
        :rtype: object
        """
        attempt = 0
        in_attempt = lambda x: not attempts or attempts > x
        while getattr(obj, attr) not in desired and in_attempt(attempt):
            util.logger.info("Wating:{0} {1}:{2}".format(obj, attr,
                                                         getattr(obj, attr)))
            sleep(interval)
            obj = fun(obj.id)
            attempt = attempt + 1
        return obj

    def power_off(self, node):
        id = node['uuid']
        server = self.nova_client.servers.get(id)
        server.shutdown()

    def power_on(self, node):
        id = node['uuid']
        server = self.nova_client.servers.get(id)
        server.startup()
