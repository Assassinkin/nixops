# -*- coding: utf-8 -*-
import datetime
import time
import sys
import boto3
import nixops.util
import nixops.resources
import botocore.exceptions
from nixops.resources.ec2_common import EC2CommonState

class dataLifecycleManagerDefinition(nixops.resources.ResourceDefinition):
    """Definition of  a data lifecycle manager"""

    @classmethod
    def get_type(cls):
        return "data-lifecycle-manager"

    @classmethod
    def get_resource_type(cls):
        return "dataLifecycleManager"

    def show_type(self):
        return "{0}".format(self.get_type())


class dataLifecycleManagerState(nixops.resources.ResourceState, EC2CommonState):
    """State of a data lifecycle manager"""

    state = nixops.util.attr_property("state", nixops.resources.ResourceState.MISSING, int)
    access_key_id = nixops.util.attr_property("accessKeyId", None)
    region = nixops.util.attr_property("region", None)
    policyId = nixops.util.attr_property("policyId", None)
    description = nixops.util.attr_property("description", None)
    executionRole = nixops.util.attr_property("executionRole", None)
    resourceTypes = nixops.util.attr_property("resourceTypes", None)
    targetTags = nixops.util.attr_property("targetTags", {}, 'json')
    excludeBootVolume = nixops.util.attr_property("excludeBootVolume", True, type=bool)
    copyTags = nixops.util.attr_property("copyTags", False, type=bool)
    tagsToAdd = nixops.util.attr_property("tagsToAdd", {}, 'json')
    ruleInterval = nixops.util.attr_property("ruleInterval", None, int)
    ruleIntervalUnit = nixops.util.attr_property("ruleIntervalUnit", None)
    ruleTime = nixops.util.attr_property("ruleTime", None)
    retainRule = nixops.util.attr_property("retainRule", None, int)

    @classmethod
    def get_type(cls):
        return "data-lifecycle-manager"

    def __init__(self, depl, name, id):
        nixops.resources.ResourceState.__init__(self, depl, name, id)
        self._conn_boto3 = None

    def _exists(self):
        return self.state != self.MISSING

    def show_type(self):
        s = super(dataLifecycleManagerState, self).show_type()
        return s

    @property
    def resource_id(self):
        return self.policyId

    def create_after(self, resources, defn):
        return {r for r in resources if
                isinstance(r, nixops.backends.ec2.EC2State)}

    # this exist only for diff engine resource and it would
    # be better to move it to the other resources aswell
    def get_client(self, service):

        if hasattr(self, '_client'):
            if self._client: return self._client
        assert self.region
        (access_key_id, secret_access_key) = nixops.ec2_utils.fetch_aws_secret_key(self.access_key_id)
        client = boto3.session.Session().client(
            service_name=service,
            region_name=self.region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key)
        return client

    def arn_from_role_name(self, role_name):

        if role_name.startswith("arn:aws:iam"): return role_name
        try:
            role_arn = self.get_client('iam').get_role(RoleName=role_name)
        except botocore.exceptions.ClientError as error:
            raise error
        return role_arn['Role']['Arn']

    def create_args_dict(self, config):
        args = dict()
        args['ExecutionRoleArn'] = self.arn_from_role_name(config['executionRole'])
        args['Description'] = config['description']
        args['State'] = "ENABLED"
        args['PolicyDetails'] = dict(

            TargetTags=[{"Key": k, "Value": config['targetTags'][k]} for k in config['targetTags']],
            Schedules=[dict(
                Name=config['name'],
                CopyTags=config['copyTags'],
                TagsToAdd=[{"Key": k, "Value": config['tagsToAdd'][k]} for k in config['tagsToAdd']],
                CreateRule=dict(
                    Interval=config['ruleInterval'],
                    IntervalUnit=config['ruleIntervalUnit'].upper(),
                    Times=[config['ruleTime'],]
                ),
                RetainRule=dict(Count=config['retainRule'])
            ),],
            Parameters=dict(ExcludeBootVolume=config['excludeBootVolume'])
        )
        with self.depl._db:
            self.description = config['description']
            self.executionRole = config['executionRole']
            self.resourceTypes = config['resourceTypes']
            self.targetTags = config['targetTags']
            self.excludeBootVolume = config['excludeBootVolume']
            self.copyTags = config['copyTags']
            self.tagsToAdd = config['tagsToAdd']
            self.ruleInterval = config['ruleInterval']
            self.ruleIntervalUnit = config['ruleIntervalUnit']
            self.ruleTime = config['ruleTime']
            self.retainRule = config['retainRule']

        return args

    def create(self, defn, check, allow_reboot, allow_recreate):
        config = defn.config
        if self.region is None:
            self.region = config['region']
        elif self.region != config['region'] or (
            self.resourceTypes != config['resourceTypes'] and self.resourceTypes != None):
            self.warn("cannot change region or resource types for an existing data lifecycle manager...")

        self.access_key_id = config['accessKeyId']

        if self.state == self.UP and (self.executionRole != config['executionRole'] or
            self.targetTags != config['targetTags'] or self.retainRule != config['retainRule'] or
            self.excludeBootVolume != config['excludeBootVolume'] or self.copyTags != config['copyTags'] or
            self.tagsToAdd != config['tagsToAdd'] or self.ruleInterval != config['ruleInterval'] or
            self.ruleIntervalUnit != config['ruleIntervalUnit'] or self.ruleTime != config['ruleTime']):
            args = self.create_args_dict(config)
            args['PolicyId'] = self.policyId
            self.log("updating data lifecycle manager `{}`... ".format(self.name))
            try:
                self.get_client('dlm').update_lifecycle_policy(**args)
            except botocore.exceptions.ClientError as error:
                raise error


        if self.state != self.UP:
            args = self.create_args_dict(config)
            args['PolicyDetails']['ResourceTypes']=[config['resourceTypes'].upper(),]
            if config['resourceTypes'] == "instance":
                args['PolicyDetails']['Schedules'][0]['VariableTags'] = [
                    dict(
                        Key="instance-id",
                        Value="$(instance-id)",
                    ),
                    dict(
                        Key="timestamp",
                        Value="$(timestamp)"
                    )
                ]

            self.log("creating data lifecycle manager `{}`... ".format(self.name))
            try:
                policy =  self.get_client('dlm').create_lifecycle_policy(**args)
            except botocore.exceptions.ClientError as error:
                raise error
            with self.depl._db:
                self.state = self.UP
                self.policyId = policy['PolicyId']
                self.resourceTypes = config['resourceTypes']

            self._update_tag(config)

    def check(self):
        if self.policyId is None:
            self.state = self.MISSING
            return
        policy = self.get_client('dlm').get_lifecycle_policies(
                    PolicyIds=[self.policyId]
                   )['Policies']
        if policy is None:
            self.state = self.MISSING
            return
        if policy[0]['State'] != 'ENABLED':
            self.warn("data lifecycle manager `{}` state is {}, please investigate..."
                .format(self.policyId, policy[0]['State']))
        return


    def _destroy(self):

        self.warn("destrying `{}` won't delete the snapshot taken by this resource ..."
                        .format(self.name))

        self.log("deleting data lifecycle manager `{}`... ".format(self.name))

        try:
            self.get_client('dlm').delete_lifecycle_policy(PolicyId=self.policyId)
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                self.warn("data lifecycle manager {0} was already deleted".format(self.name))
            else:
                raise e

    def destroy(self, wipe=False):
        if not self._exists(): return True

        self._destroy()
        return True