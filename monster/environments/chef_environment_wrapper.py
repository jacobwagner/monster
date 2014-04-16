"""
Chef Environment
"""

from monster import util
from chef import Environment as ChefEnvironment
from base_environment_wrapper import BaseEnvironmentWrapper


class ChefEnvironmentWrapper(BaseEnvironmentWrapper):

    def __init__(self, name, local_api, chef_server_name=None, remote_api=None,
                 description='', default=None, override=None):
        super(ChefEnvironmentWrapper, self).__init__(name, description)
        self.cookbook_versions = {}
        self.json_class = "Chef::Environment"
        self.chef_type = "environment"
        self.default_attributes = default or {}
        self.override_attributes = override or {}
        self.local_api = local_api
        self.remote_api = remote_api
        self.chef_server_name = chef_server_name
        self.save()

    def __repr__(self):
        """
        Exclude unserializable chef objects
        """

        chef_dict = {
            "chef_type": self.chef_type,
            "cookbook_versions": self.cookbook_versions,
            "description": self.description,
            "json_class": self.json_class,
            "name": self.name,
            "default_attributes": self.default_attributes,
            "override_attributes": self.override_attributes
        }
        return str(chef_dict)

    def add_override_attr(self, key, value):
        self.override_attributes[key] = value
        self.save()

    def add_default_attr(self, key, value):
        self.default_attributes[key] = value
        self.save()

    def del_override_attr(self, key):
        del self.override_attributes[key]
        self.save()

    def del_default_attr(self, key):
        del self.default_attributes[key]
        self.save()

    def save_locally(self):
        if self.remote_api:
            env = ChefEnvironment(self.name, api=self.remote_api)
            self.override_attributes = env.override_attributes
            self.default_attributes = env.default_attributes
            self.save()

    def save(self):

        # Load local chef env
        env = ChefEnvironment(self.name, api=self.local_api)

        # update chef env with local object info
        for attr in self.__dict__:
            util.logger.debug("{0}: {1}".format(attr, self.__dict__[attr]))
            setattr(env, attr, self.__dict__[attr])

        # Save local/remote
        env.save(self.local_api)
        if self.remote_api:
            try:
                env.save(self.remote_api)
            except Exception as e:
                util.logger.error("Remote env error:{0}".format(e))

    def destroy(self):
        ChefEnvironment(self.name, self.local_api).delete()

    @property
    def deployment_attributes(self):
        return self.override_attributes.get('deployment', {})

    @property
    def features(self):
        return self.deployment_attributes.get('features', {})

    @property
    def branch(self):
        return self.deployment_attributes.get('branch', None)

    @property
    def os_name(self):
        return self.deployment_attributes.get('os_name', None)

    @property
    def nodes(self):
        return self.deployment_attributes.get('nodes', [])

    @property
    def provisioner(self):
        return self.deployment_attributes.get('provisioner', "razor2")

    @property
    def product(self):
        return self.deployment_attributes.get('product', None)
