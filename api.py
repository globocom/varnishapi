import os
import sqlite3
import subprocess
import syslog
from md5 import md5
from flask import Flask, request

api = Flask(__name__)
access_key = os.environ.get("ACCESS_KEY")
secret_key = os.environ.get("SECRET_KEY")
ami_id = os.environ.get("AMI_ID")
subnet_id = os.environ.get("SUBNET_ID")
default_db_name = "varnishapi.db"
vcl_template = """backend default {{
    .host = \\"{0}\\";
    .port = \\"80\\";
}}
"""


@api.route("/resources", methods=["POST"])
def create_instance():
    reservations = _create_ec2_instance()
    _store_instance_and_app(reservations, request.form.get("name")) # check if name is present
    return "", 201


@api.route("/resources/<name>", methods=["DELETE"])
def delete_instance(name):
    instance_id = _get_instance_id(service_instance=name)
    _delete_ec2_instance(instance_id=instance_id)
    _delete_from_database(name)
    return "", 200


@api.route("/resources/<name>", methods=["POST"])
def bind(name):
    i_id = _get_instance_id(service_instance=name)
    i_ip = _get_instance_ip(instance_id=i_id)
    app_ip = request.form.get("hostname")
    _update_vcl_file(instance_address=i_ip, app_address=app_ip)
    return "null", 201


@api.route("/resources/<name>/hostname/<host>", methods=["DELETE"])
def unbind(name, host):
    i_id = _get_instance_id(service_instance=name)
    i_ip = _get_instance_ip(instance_id=i_id)
    _clean_vcl_file(instance_address=i_ip)
    return "", 200


def _get_instance_ip(instance_id):
    from boto.ec2.connection import EC2Connection
    conn = EC2Connection(access_key, secret_key)
    reservations = conn.get_all_instances(instance_ids=[instance_id])
    if len(reservations) != 1 or len(reservations[0].instances) != 1:
        return "" #throw exception?
    return reservations[0].instances[0].private_ip_address


def _rand_stdout_filename(salt):
    tail = md5(salt).hexdigest()
    return "/tmp/varnish-out-{0}".format(tail)

def _clean_vcl_file(instance_address):
    out = file(_rand_stdout_filename(instance_address), "w+")
    cmd = 'sudo bash -c \'echo "" > /etc/varnish/default.vcl\''
    exit_status = subprocess.call(["ssh", instance_address, "-l", "ubuntu", cmd], stdout=out, stderr=subprocess.STDOUT)
    out.seek(0)
    out = out.read()
    syslog.syslog(syslog.LOG_ERR, out)
    if exit_status != 0:
        raise Exception("Unable to clean vcl file from instance with ip {0}. Error was: {1}".format(instance_address, out))


def _update_vcl_file(instance_address, app_address):
    out = file(_rand_stdout_filename(instance_address), "w+")
    cmd = 'sudo bash -c "echo \'{0}\' > /etc/varnish/default.vcl && service varnish reload"'.format(vcl_template.format(app_address))
    exit_status = subprocess.call(["ssh", instance_address, "-l", "ubuntu", cmd], stdout=out, stderr=subprocess.STDOUT)
    out.seek(0)
    out = out.read()
    syslog.syslog(syslog.LOG_ERR, out)
    if exit_status != 0:
        raise Exception("Unable to update vcl file from instance with ip {0}. Error was: {1}".format(instance_address, out))


def _delete_from_database(name):
    c = conn.cursor()
    c.execute("delete from instance_app where app_name=?", [name])
    conn.commit()


def _get_instance_id(service_instance):
    c = conn.cursor()
    query = "select instance_id from instance_app where app_name=? limit 1"
    c.execute(query, [service_instance])
    result = c.fetchall()
    if len(result) == 0 or len(result[0]) == 0:
        return ""
    return result[0][0]


def _delete_ec2_instance(instance_id):
    from boto.ec2.connection import EC2Connection
    conn = EC2Connection(access_key, secret_key)
    return conn.terminate_instances(instance_ids=[instance_id])


def _create_ec2_instance():
    from boto.ec2.connection import EC2Connection
    conn = EC2Connection(access_key, secret_key)
    return conn.run_instances(image_id=ami_id, subnet_id=subnet_id)


def _store_instance_and_app(reservations, app_name):
    instance_apps = []
    for r in reservations:
        for i in r.instances:
            instance_apps.append((i.id, app_name))
    c = conn.cursor()
    c.executemany("insert into instance_app values (?, ?)", instance_apps)
    conn.commit()


def _get_database_name():
    if os.environ.get("DB_PATH"): # this env var must be an absolute path
        return os.environ["DB_PATH"]
    return os.path.realpath(os.path.join(__file__, "../", default_db_name))


conn = sqlite3.connect(_get_database_name())
if __name__ == "__main__":
    api.run()
