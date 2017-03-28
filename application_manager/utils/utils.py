# Copyright 2017 LSD-UFCG.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


import datetime
import json

from application_manager.exceptions import *

from keystoneauth1.identity import v3
from keystoneauth1 import session
from saharaclient.api.client import Client as saharaclient
from subprocess import *

import sys

R_PREFIX = 'Rscript '
PYTHON_PREFIX = 'python '

class Shell(object):
    def execute_r_script(self, script, args):
        command = R_PREFIX + script + " " + " ".join(args)
        p_status = Popen(command, shell=True, stdout=PIPE, stderr=PIPE)
        out, err = p_status.communicate()
        try:
            print(out, err)
            value = float(out)
            return value
        except Exception as e:
            print(e)
            print("Error message captured:", err)
            raise

    def execute_async_python_script(self, script, args):
        args = [str(i) + "\n" for i in args]
        command = PYTHON_PREFIX + script + " " + " ".join(args)
        p = Popen(command, shell=True, stdout=PIPE, stderr=PIPE)
        for line in iter(p.stdout.readline, ''):
            line = line.replace('\r', '').replace('\n', '')
            print(line)
            sys.stdout.flush()


class OSClientHelpers(object):
    def __init__(self, logger):
        self.logger = logger

    def get_sahara_client(self, token, project_id, auth_ip):
        auth = v3.Token(auth_url=auth_ip + ':5000/v3',
                        token=token,
                        project_id=project_id)
        ses = session.Session(auth=auth)

        return saharaclient('1.1', session=ses)

    def get_cluster_status(self, sahara, cluster_id):
        cluster = sahara.clusters.get(cluster_id)
        return cluster.status

    def get_cluster_by_name(self, sahara, cluster_name):
        self.logger.log("Searching for cluster named " + cluster_name)
        query = {'name': cluster_name}
        clusters = sahara.clusters.list(query)
        if len(clusters) > 0:
            return clusters[0]
        return None

    def get_timestamp_raw(self):
        return datetime.datetime.now().strftime('%Y%m%d%H%M%S')

    def get_worker_host_ip(self, worker_id):
        # FIXME hardcoded
        hosts = ["c4-compute11", "c4-compute12"]
        for host in hosts:
            if int(check_output("ssh root@%s test -e "
                                "\"/var/lib/nova/instances/%s\" && echo "
                                "\"1\" || echo \"0\"" % (host, worker_id),
                                shell=True)) == 1:
                return host
        return None

    def get_job_status(self, sahara, job_id):
        return sahara.job_executions.get(job_id).info['status']

    def is_job_completed(self, job_status):
        success = ('SUCCEEDED', ' ', '')
        succeeded = job_status in success

        return succeeded

    def is_job_failed(self, job_status):
        fails = ('KILLED', 'FAILED', 'TIMEOUT', 'DONEWITHERROR')
        return job_status in fails

    def is_on_same_host(self, nova, instance_id, host):
        instance_ref = nova.servers.get(instance_id)
        instance_host = instance_ref.__dict__['OS-EXT-SRV-ATTR:host']
        return instance_host == host

    def pick_random_instance(self, sahara, nova, cluster_id, host):
        cluster = sahara.clusters.get(cluster_id)
        node_groups = cluster.node_groups
        for n in node_groups:
            if "slave" in n['node_group_name']:
                for ins in n['instances']:
                    instance_id = ins
                    if self.is_on_same_host(nova, instance_id, host):
                        return instance_id
        self.logger.log("There is no slave instance on host: %s" % host)
        return None

    def _get_worker_instances(self, sahara, cluster_id):
        instances = []
        cluster = sahara.clusters.get(cluster_id)
        node_groups = cluster.node_groups
        for node_group in node_groups:
            if 'datanode' in node_group['node_processes']:
                for instance in node_group['instances']:
                    instance_name = instance
                    instances.append(instance_name)
        return instances

    def _get_master_instance(self, sahara, cluster_id, type):
        cluster = sahara.clusters.get(cluster_id)
        node_groups = cluster.node_groups
        for node_group in node_groups:
            if 'namenode' in node_group['node_processes']:
                for instance in node_group['instances']:
                    return instance

        return None


class ActionDispatcher(object):
    """Maps method name to local methods through action name."""

    def dispatch(self, *args, **kwargs):
        """Find and call local method."""
        action = kwargs.pop('action', 'default')
        action_method = getattr(self, str(action), self.default)
        return action_method(*args, **kwargs)

    def default(self, data):
        raise NotImplementedError()

class DictSerializer(ActionDispatcher):
    """Default request body serialization."""

    def serialize(self, data, action='default'):
        return self.dispatch(data, action=action)

    def default(self, data):
        return ""


class JSONDictSerializer(DictSerializer):
    """Default JSON request body serialization."""

    def default(self, data):
        def sanitizer(obj):
            if isinstance(obj, datetime.datetime):
                _dtime = obj - datetime.timedelta(microseconds=obj.microsecond)
                return _dtime.isoformat()
            return six.text_type(obj)
        return json.dumps(data, default=sanitizer)


class TextDeserializer(ActionDispatcher):
    """Default request body deserialization."""

    def deserialize(self, datastring, action='default'):
        return self.dispatch(datastring, action=action)

    def default(self, datastring):
        return {}


class JSONDeserializer(TextDeserializer):

    def _from_json(self, datastring):
        try:
            return json.loads(datastring)
        except ValueError:
            msg = ("cannot understand JSON")
            raise MalformedRequestBody(msg)

    def default(self, datastring):
        return {'body': self._from_json(datastring)}
