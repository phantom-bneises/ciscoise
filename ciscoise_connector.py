# File: ciscoise_connector.py
#
# Copyright (c) 2014-2021 Splunk Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied. See the License for the specific language governing permissions
# and limitations under the License.
#
#
# Phantom imports
import json

import phantom.app as phantom
import requests
import xmltodict
from cerberus import Validator
from phantom.action_result import ActionResult
from phantom.base_connector import BaseConnector
from requests.auth import HTTPBasicAuth

# THIS Connector imports
from ciscoise_consts import *


class CiscoISEConnector(BaseConnector):
    # actions supported by this script
    ACTION_ID_LIST_SESSIONS = "list_sessions"
    ACTION_ID_TERMINATE_SESSION = "terminate_session"
    ACTION_ID_LOGOFF_SYSTEM = "logoff_system"
    ACTION_ID_QUARANTINE_SYSTEM = "quarantine_device"
    ACTION_ID_UNQUARANTINE_SYSTEM = "unquarantine_device"
    ACTION_ID_LIST_ENDPOINTS = "list_endpoints"
    ACTION_ID_GET_ENDPOINT = "get_endpoint"
    ACTION_ID_UPDATE_ENDPOINT = "update_endpoint"
    ACTION_ID_LIST_RESOURCES = "list_resources"
    ACTION_ID_GET_RESOURCES = "get_resources"
    ACTION_ID_DELETE_RESOURCE = "delete_resource"
    ACTION_ID_CREATE_RESOURCE = "create_resource"
    ACTION_ID_UPDATE_RESOURCE = "update_resource"
    ACTION_ID_APPLY_POLICY = "apply_policy"
    ACTION_ID_CLEAR_POLICY = "clear_policy"
    ACTION_ID_LIST_POLICIES = "list_policies"
    ACTION_ID_CREATE_POLICY = "create_policy"
    ACTION_ID_DELETE_POLICY = "delete_policy"

    def __init__(self):

        # Call the BaseConnectors init first
        super(CiscoISEConnector, self).__init__()

        self._base_url = None
        self._auth = None
        self._ha_device = None
        self._ers_auth = None

    def initialize(self):

        config = self.get_config()

        self._auth = HTTPBasicAuth(config[phantom.APP_JSON_USERNAME], config[phantom.APP_JSON_PASSWORD])
        ers_user = config.get("ers_user", None)
        self._ha_device = config.get("ha_device", None)
        if ers_user is not None:
            self._ers_auth = HTTPBasicAuth(config.get("ers_user"), config.get("ers_password"))
        self._base_url = "https://{0}".format(config[phantom.APP_JSON_DEVICE])

        if self._ha_device:
            self._ha_device_url = "https://{0}".format(self._ha_device)
            self._call_ers_api = self._ha_device_wrapper(self._call_ers_api)
            self._call_rest_api = self._ha_device_wrapper(self._call_rest_api)

        return phantom.APP_SUCCESS

    def _ha_device_wrapper(self, func):
        def make_another_call(*args, **kwargs):
            self.debug_print("Making call to primary device")
            ret_val, ret_data = func(*args, **kwargs)

            if phantom.is_fail(ret_val) and self._ha_device:
                self.debug_print("Call to first device failed. Data returned: {}".format(ret_data))
                self.debug_print("Making call to secondary device")
                ret_val, ret_data = func(try_ha_device=True, *args, **kwargs)

            return ret_val, ret_data

        return make_another_call

    def _call_ers_api(self, endpoint, action_result, data=None, allow_unknown=True, method="get", try_ha_device=False):
        if self._ers_auth is None:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERS_CRED_MISSING), None
        url = "{0}{1}".format(self._base_url, endpoint)
        if try_ha_device:
            url = "{0}{1}".format(self._ha_device_url, endpoint)

        ret_data = None

        config = self.get_config()
        verify = config[phantom.APP_JSON_VERIFY]
        try:
            request_func = getattr(requests, method)
        except AttributeError as e:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_REST_API, e), ret_data
        try:
            headers = {"Content-Type": "application/json", "ACCEPT": "application/json"}
            resp = request_func(  # nosemgrep: python.requests.best-practice.use-timeout.use-timeout
                url,
                json=data,
                verify=verify,
                headers=headers,
                auth=self._ers_auth
            )
        except Exception as e:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_REST_API, e), ret_data

        if not (200 <= resp.status_code < 399):
            error_message = resp.text
            if resp.status_code == 401:
                error_message = "The request has not been applied because it lacks valid authentication credentials" \
                                " for the target resource."
            elif resp.status_code == 404:
                error_message = "Resource not found"
            return (
                action_result.set_status(
                    phantom.APP_ERROR,
                    CISCOISE_ERR_REST_API_ERR_CODE,
                    code=resp.status_code,
                    message=error_message
                ),
                ret_data
            )

        if not resp.text:
            return (
                action_result.set_status(phantom.APP_SUCCESS, "Empty response and no information in the header"),
                None
            )

        ret_data = json.loads(resp.text)

        return phantom.APP_SUCCESS, ret_data

    def _call_rest_api(self, endpoint, action_result, schema=None, data=None, allow_unknown=True, try_ha_device=False):
        url = "{0}{1}".format(self._base_url, endpoint)
        if try_ha_device:
            url = "{0}{1}".format(self._ha_device_url, endpoint)

        ret_data = None

        config = self.get_config()
        verify = config[phantom.APP_JSON_VERIFY]

        try:
            resp = requests.get(  # nosemgrep: python.requests.best-practice.use-timeout.use-timeout
                url,
                verify=verify,
                auth=self._auth)
        except Exception as e:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_REST_API, e), ret_data

        if resp.status_code != 200:
            return (
                action_result.set_status(
                    phantom.APP_ERROR,
                    CISCOISE_ERR_REST_API_ERR_CODE,
                    code=resp.status_code,
                    message=resp.text,
                ),
                ret_data,
            )

        action_result.add_debug_data(resp.text)
        xml = resp.text

        try:
            response_dict = xmltodict.parse(xml)
        except Exception as e:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_UNABLE_TO_PARSE_REPLY, e), ret_data

        ret_data = response_dict

        if schema is not None:
            v = Validator(schema, allow_unknown=allow_unknown)
            if v.validate(ret_data) is False:
                action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_UNABLE_TO_PARSE_REPLY)
                action_result.append_to_message(v.errors)
                return action_result.get_status(), ret_data

        return phantom.APP_SUCCESS, ret_data

    def _map_resource_type(self, resource_type, action_result, *args):
        try:
            return MAP_RESOURCE[resource_type][0]
        except Exception as ex:  # noqa: F841
            return action_result.set_status(phantom.APP_ERROR, "Invalid resource type")

    def _list_sessions(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        summary = action_result.update_summary({CISCOISE_JSON_TOTAL_SESSIONS: 0})

        ret_data = None

        ret_val, ret_data = self._call_rest_api(ACTIVE_LIST_REST, action_result)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        if "activeList" not in ret_data:
            return action_result.set_status(phantom.APP_SUCCESS)

        active_sessions = ret_data["activeList"].get("activeSession")

        if active_sessions is None:
            return action_result.set_status(phantom.APP_SUCCESS)

        # Convert the dict into list, so the rest of the code is the same
        if isinstance(active_sessions, dict):
            act_sess_list = []
            act_sess_list.append(active_sessions)
            active_sessions = act_sess_list

        for session in active_sessions:

            action_result.add_data(session)

            # Init the value of the quarantine status of the session to unknown
            session["is_quarantined"] = "Unknown"

            # Get the quarantined state of the mac address
            is_quarantined_rest = "{0}/{1}".format(IS_MAC_QUARANTINED_REST, session["calling_station_id"])

            ret_val, ret_data = self._call_rest_api(is_quarantined_rest, action_result, IS_MAC_QUARAN_RESP_SCHEMA)

            if phantom.is_fail(ret_val):
                continue

            # Can safely access the members of ret_data, since they have been parsed as by the rules of
            # IS_MAC_QUARAN_RESP_SCHEMA
            session["is_quarantined"] = "Yes" if ret_data["EPS_RESULT"]["userData"] == "true" else "No"

        summary.update({CISCOISE_JSON_TOTAL_SESSIONS: len(active_sessions)})

        return action_result.set_status(phantom.APP_SUCCESS)

    def _list_endpoints(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        ret_data = None
        endpoint = ERS_ENDPOINT_REST

        mac_filter = param.get("mac_address", None)
        if mac_filter is not None:
            endpoint = ERS_ENDPOINT_REST + "?filter=mac.EQ." + mac_filter

        ret_val, ret_data = self._call_ers_api(endpoint, action_result)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        total = ret_data["SearchResult"]["total"]

        action_result.update_summary({"endpoints_found": total})

        action_result.add_data(ret_data)

        return action_result.set_status(phantom.APP_SUCCESS, CISCOISE_SUCC_LIST_ENDPOINTS.format(total))

    def _get_endpoint(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        ret_data = None
        endpoint = ERS_ENDPOINT_REST + "/" + param["endpoint_id"]

        ret_val, ret_data = self._call_ers_api(endpoint, action_result)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        action_result.add_data(ret_data)

        return action_result.set_status(phantom.APP_SUCCESS, CISCOISE_SUCC_GET_ENDPOINT)

    def _update_endpoint(self, param):

        action_result = self.add_action_result(ActionResult(dict(param)))

        endpoint = ERS_ENDPOINT_REST + "/" + param["endpoint_id"]
        attribute = param.get("attribute", None)
        attribute_value = param.get("attribute_value", None)
        custom_attribute = param.get("custom_attribute", None)
        custom_attribute_value = param.get("custom_attribute_value", None)

        final_data = {"ERSEndPoint": {}}

        if not (attribute or custom_attribute or attribute_value or custom_attribute_value):
            return action_result.set_status(phantom.APP_ERROR, "Please specify attribute or custom attribute")

        if (attribute is not None) ^ (attribute_value is not None):
            return action_result.set_status(phantom.APP_ERROR, "Please specify both attribute and attribute value")
        elif attribute and attribute_value:
            final_data["ERSEndPoint"][attribute] = attribute_value

        if (custom_attribute is not None) ^ (custom_attribute_value is not None):
            return action_result.set_status(
                phantom.APP_ERROR,
                "Please specify both custom attribute and custom attribute value"
            )
        elif custom_attribute and custom_attribute_value:
            custom_attribute_dict = {"customAttributes": {custom_attribute: custom_attribute_value}}
            final_data["ERSEndPoint"]["customAttributes"] = custom_attribute_dict

        ret_val, ret_data = self._call_ers_api(endpoint, action_result, data=final_data, method="put")
        action_result.add_data(ret_data)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        return action_result.set_status(phantom.APP_SUCCESS, "Endpoint Updated")

    def _quarantine_system(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        ret_data = None

        mac_ip_address = param[phantom.APP_JSON_IP_MACADDRESS]

        if phantom.is_mac(mac_ip_address):
            endpoint = "{0}/{1}".format(QUARANTINE_MAC_REST, mac_ip_address)
        elif phantom.is_ip(mac_ip_address):
            endpoint = "{0}/{1}".format(QUARANTINE_IP_REST, mac_ip_address)
        else:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_MAC_AND_IP_NOT_SPECIFIED)

        ret_val, ret_data = self._call_rest_api(endpoint, action_result, QUARANTINE_RESP_SCHEMA)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        action_result.add_data(ret_data)

        # Can safely access the members of ret_data, since they have been parsed as by the rules of
        # QUARANTINE_RESP_SCHEMA
        status = ret_data["EPS_RESULT"]["status"]

        if status == "Failure":
            return action_result.set_status(
                phantom.APP_ERROR,
                CISCOISE_ERR_ACTION_FAILED,
                error_code=ret_data["EPS_RESULT"]["errorCode"]
            )

        # In cases where the radius authentication failed, the status is STILL set to success,
        # but failureType and failureMessage keys are added to the ret_data, so need to check for those
        failure_type = phantom.get_value(ret_data["EPS_RESULT"], "failureType")
        failure_msg = phantom.get_value(ret_data["EPS_RESULT"], "failureMessage")

        if (failure_type is not None) or (failure_msg is not None):
            action_result.set_status(
                phantom.APP_ERROR, CISCOISE_ERR_ACTION_FAILED, error_code=ret_data["EPS_RESULT"]["errorCode"]
            )
            if failure_type is not None:
                action_result.append_to_message(failure_type)
            if failure_msg is not None:
                action_result.append_to_message(failure_msg)
            return action_result.get_status()

        return action_result.set_status(phantom.APP_SUCCESS, CISCOISE_SUCC_SYSTEM_QUARANTINED)

    def _unquarantine_system(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        ret_data = None

        mac_ip_address = param[phantom.APP_JSON_IP_MACADDRESS]

        if phantom.is_mac(mac_ip_address):
            endpoint = "{0}/{1}".format(UNQUARANTINE_MAC_REST, mac_ip_address)
        elif phantom.is_ip(mac_ip_address):
            endpoint = "{0}/{1}".format(UNQUARANTINE_IP_REST, mac_ip_address)
        else:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_MAC_AND_IP_NOT_SPECIFIED)

        ret_val, ret_data = self._call_rest_api(endpoint, action_result, QUARANTINE_RESP_SCHEMA)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        action_result.add_data(ret_data)

        status = ret_data["EPS_RESULT"]["status"]

        if status == "Failure":
            return action_result.set_status(
                phantom.APP_ERROR, CISCOISE_ERR_ACTION_FAILED, error_code=ret_data["EPS_RESULT"]["errorCode"]
            )

        # In cases where the radius authentication failed, the status is STILL set to success,
        # but failureType and failureMessage keys are added to the ret_data, so need to check for those
        failure_type = phantom.get_value(ret_data["EPS_RESULT"], "failureType")
        failure_msg = phantom.get_value(ret_data["EPS_RESULT"], "failureMessage")

        if (failure_type is not None) or (failure_msg is not None):
            action_result.set_status(
                phantom.APP_ERROR,
                CISCOISE_ERR_ACTION_FAILED,
                error_code=ret_data["EPS_RESULT"]["errorCode"],
            )
            if failure_type is not None:
                action_result.append_to_message(failure_type)
            if failure_msg is not None:
                action_result.append_to_message(failure_msg)
            return action_result.get_status()

        return action_result.set_status(
            phantom.APP_SUCCESS, CISCOISE_SUCC_SYSTEM_UNQUARANTINED
        )

    def _logoff_system(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        ret_data = None

        server = param[CISCOISE_JSON_SERVER]
        mac_address = param[CISCOISE_JSON_MACADDR]
        port = 2  # 0 is default, 1 is bounce, 2 is shutdown

        endpoint = "{0}/{1}/{2}/{3}".format(REAUTH_MAC_REST, server, mac_address, port)

        ret_val, ret_data = self._call_rest_api(endpoint, action_result)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        action_result.add_data(ret_data)

        remote_coa = ret_data.get("remoteCoA")

        if remote_coa is None:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_PARSE_REPLY)

        result = remote_coa.get("results")

        if result is None:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_PARSE_REPLY)

        if result == "false":
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_LOGOFF_SYSTEM)

        return action_result.set_status(phantom.APP_SUCCESS)

    def _terminate_session(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        ret_data = None

        mac_address = param[phantom.APP_JSON_MACADDRESS]
        port = 2  # 0 is default, 1 is bounce, 2 is shutdown

        # First try to find the server that we should use
        endpoint = "{0}/{1}".format(MAC_SESSION_DETAILS_REST, mac_address)

        ret_val, ret_data = self._call_rest_api(endpoint, action_result, MAC_SESSION_RESP_SCHEMA)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        acs_server = ret_data["sessionParameters"]["acs_server"]

        # now terminate the session
        endpoint = "{0}/{1}/{2}/{3}".format(DISCONNECT_MAC_REST, acs_server, mac_address, port)

        ret_val, ret_data = self._call_rest_api(endpoint, action_result)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        remote_coa = ret_data.get("remoteCoA")

        if remote_coa is None:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_PARSE_REPLY)

        result = remote_coa.get("results")

        if result is None:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_PARSE_REPLY)

        if result == "false":
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_TERMINATE_SESSION)

        return action_result.set_status(phantom.APP_SUCCESS, CISCOISE_SUCC_SESSION_TERMINATED)

    def _paginator(self, endpoint, action_result, payload=None, limit=None):

        items_list = list()

        if not payload:
            payload = {}

        page = 1
        payload["size"] = DEFAULT_MAX_RESULTS
        payload["page"] = page

        while True:
            ret_val, items = self._call_ers_api(endpoint, action_result, data=payload)

            if phantom.is_fail(ret_val):
                return None

            items_list.extend(items.get("SearchResult", {}).get("resources"))

            if limit and len(items_list) >= limit:
                return items_list[:limit]

            if len(items.get("SearchResult", {}).get("resources")) < DEFAULT_MAX_RESULTS:
                break

            if len(items_list) == items.get("SearchResult", {}).get("total"):
                break

            page = page + 1
            payload["page"] = page

        return items_list

    def _list_resources(self, param):

        action_result = self.add_action_result(ActionResult(dict(param)))
        resource = self._map_resource_type(param["resource"], action_result)
        max_result = param.get("max_results")
        endpoint = ERS_RESOURCE_REST.format(resource=resource)

        try:
            if max_result:
                max_result = int(max_result)
        except ValueError as ex:  # noqa: F841
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_INVALID_PARAM.format(param="max_result"))

        if max_result is not None and max_result <= 0:
            return action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_INVALID_PARAM.format(param="max_result"))

        resources = self._paginator(endpoint, action_result, limit=max_result)

        if resources is None:
            return action_result.get_status()

        for resource in resources:
            action_result.add_data(resource)

        summary = action_result.update_summary({})
        summary["resources_returned"] = action_result.get_data_size()

        return action_result.set_status(phantom.APP_SUCCESS)

    def _get_resources(self, param):

        action_result = self.add_action_result(ActionResult(dict(param)))

        resource = MAP_RESOURCE[param["resource"]][0]
        resource_id = param.get("resource_id")
        key = param.get("key")
        value = param.get("value")

        if not resource_id and not key:
            return action_result.set_status(
                phantom.APP_ERROR,
                "Please enter either 'resource id' or 'key' and 'value' to get the details of a particular resource",
            )
        elif key and not value:
            return action_result.set_status(phantom.APP_ERROR, "Please enter value for the key")
        if not resource_id and (key and value):
            resource_filter = "filter={0}.EQ.{1}".format(key, value)
            endpoint = "{0}?{1}".format(ERS_RESOURCE_REST.format(resource=resource), resource_filter)

            resources = self._paginator(endpoint, action_result)

            if resources is None:
                return action_result.get_status()

            for resource in resources:
                action_result.add_data(resource)

            summary = action_result.update_summary({})
            summary["resources_returned"] = len(resources)

            return action_result.set_status(phantom.APP_SUCCESS)

        endpoint = "{0}/{1}".format(ERS_RESOURCE_REST.format(resource=resource), resource_id)

        ret_val, resp = self._call_ers_api(endpoint, action_result)
        if phantom.is_fail(ret_val):
            return action_result.get_status()

        summary = action_result.update_summary({})
        summary["resource_id"] = resource_id

        action_result.add_data(resp.get(MAP_RESOURCE[param["resource"]][1]))

        return action_result.set_status(phantom.APP_SUCCESS)

    def _delete_resource(self, param):

        action_result = self.add_action_result(ActionResult(dict(param)))

        resource = MAP_RESOURCE[param["resource"]][0]
        resource_id = param["resource_id"]

        endpoint = "{0}/{1}".format(ERS_RESOURCE_REST.format(resource=resource), resource_id)

        ret_val, resp = self._call_ers_api(endpoint, action_result, method="delete")
        if phantom.is_fail(ret_val):
            return action_result.get_status()

        return action_result.set_status(phantom.APP_SUCCESS, "Resource deleted successfully")

    def _create_resource(self, param):

        action_result = self.add_action_result(ActionResult(dict(param)))

        resource = MAP_RESOURCE[param["resource"]][0]
        try:
            resource_json = json.loads(param["resource_json"])
        except Exception as ex:  # noqa: F841
            return action_result.set_status(phantom.APP_ERROR, "Error parsing json")

        endpoint = "{0}".format(ERS_RESOURCE_REST.format(resource=resource))

        ret_val, resp = self._call_ers_api(endpoint, action_result, data=resource_json, method="post")
        if phantom.is_fail(ret_val):
            return action_result.get_status()

        return action_result.set_status(phantom.APP_SUCCESS, "Resource created successfully")

    def _update_resource(self, param):

        action_result = self.add_action_result(ActionResult(dict(param)))

        resource = MAP_RESOURCE[param["resource"]][0]
        resource_key = MAP_RESOURCE[param["resource"]][1]
        resource_id = param["resource_id"]
        key = param["key"]
        value = param["value"]

        endpoint = "{0}/{1}".format(ERS_RESOURCE_REST.format(resource=resource), resource_id)

        data_dict = {resource_key: {}}
        data_dict[resource_key][key] = value
        ret_val, resp = self._call_ers_api(
            endpoint, action_result, data=data_dict, method="put"
        )
        if phantom.is_fail(ret_val):
            return action_result.get_status()

        return action_result.set_status(phantom.APP_SUCCESS, "Resource updated successfully")

    def _handle_policy_change(self, action_result, param, change_type="apply"):
        ret_data = None
        policy_name = param.get("policy_name", None)
        ip_mac_address = param.get("ip_mac_address", None)

        payload = {
            "OperationAdditionalData": {
                "additionalData": [
                    {"name": "macAddress", "value": ip_mac_address},
                    {"name": "policyName", "value": policy_name},
                ]
            }
        }

        if phantom.is_mac(ip_mac_address):
            payload["OperationAdditionalData"]["additionalData"][0]["name"] = "macAddress"
        elif phantom.is_ip(ip_mac_address):
            payload["OperationAdditionalData"]["additionalData"][0]["name"] = "ipAddress"
        else:
            return (
                action_result.set_status(phantom.APP_ERROR, CISCOISE_ERR_MAC_AND_IP_NOT_SPECIFIED),
                ret_data,
            )

        endpoint = ERS_ENDPOINT_ANC_APPLY
        if change_type == "clear":
            endpoint = ERS_ENDPOINT_ANC_CLEAR

        ret_val, ret_data = self._call_ers_api(endpoint, action_result, data=payload, method="put")

        if phantom.is_fail(ret_val):
            return action_result.get_status(), ret_data

        return action_result.set_status(phantom.APP_SUCCESS), ret_data

    def _apply_policy(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        ret_val, ret_data = self._handle_policy_change(action_result, param, "apply")

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        action_result.add_data(ret_data)
        return action_result.set_status(phantom.APP_SUCCESS, "Policy applied")

    def _clear_policy(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        ret_val, ret_data = self._handle_policy_change(action_result, param, "clear")

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        action_result.add_data(ret_data)
        return action_result.set_status(phantom.APP_SUCCESS, "Policy cleared")

    def _list_policies(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        ret_data = None
        endpoint = ERS_POLICIES

        ret_val, ret_data = self._call_ers_api(endpoint, action_result)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        total = ret_data["SearchResult"]["total"]
        policies = ret_data["SearchResult"]["resources"]

        for policy in policies:
            endpoint = f"{ERS_POLICIES}/{policy['id']}"

            ret_val, ret_data = self._call_ers_api(endpoint, action_result)
            if phantom.is_fail(ret_val):
                return action_result.get_status()
            data = ret_data["ErsAncPolicy"]
            data['actions'] = ', '.join(data['actions'])
            action_result.add_data(data)

        action_result.update_summary({"policies_found": total})

        return action_result.set_status(phantom.APP_SUCCESS)

    def _delete_policy(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        ret_data = None
        endpoint = f"{ERS_POLICIES}/{param['policy_id']}"

        ret_val, ret_data = self._call_ers_api(endpoint, action_result, method="delete")

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        return action_result.set_status(phantom.APP_SUCCESS, "Policy deleted")

    def _create_policy(self, param):

        ret_val = phantom.APP_SUCCESS

        action_result = self.add_action_result(ActionResult(dict(param)))

        name = param["name"]
        quarantine = param.get("quarantine", False)
        port_bounce = param.get("port_bounce", False)
        re_authenticate = param.get("re_authenticate", False)
        shutdown = param.get("shutdown", False)

        if not (quarantine or port_bounce or re_authenticate or shutdown):
            return action_result.set_status(phantom.APP_ERROR, "Atleast one action type is required")

        body = {
            "ErsAncPolicy": {
                "name": name,
                "actions": []
            }
        }

        if quarantine:
            body["ErsAncPolicy"]["actions"].append("QUARANTINE")
        if port_bounce:
            body["ErsAncPolicy"]["actions"].append("PORTBOUNCE")
        if re_authenticate:
            body["ErsAncPolicy"]["actions"].append("RE_AUTHENTICATE")
        if shutdown:
            body["ErsAncPolicy"]["actions"].append("SHUTDOWN")

        ret_data = None
        endpoint = f"{ERS_POLICIES}"

        ret_val, ret_data = self._call_ers_api(endpoint, action_result, method="post", data=body)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        return action_result.set_status(phantom.APP_SUCCESS, 'Policy created')

    def _test_connectivity_to_device(self, base_url, verify=True):
        try:
            rest_endpoint = "{0}{1}".format(base_url, ACTIVE_LIST_REST)
            self.save_progress(phantom.APP_PROG_CONNECTING_TO_ELLIPSES, base_url)
            resp = requests.get(  # nosemgrep: python.requests.best-practice.use-timeout.use-timeout
                rest_endpoint,
                auth=self._auth,
                verify=verify)
        except Exception as e:
            self.debug_print("Exception is test connectivity: {}".format(e))
            return self.set_status_save_progress(phantom.APP_ERROR, CISCOISE_ERR_TEST_CONNECTIVITY_FAILED)

        if resp.status_code == 200:
            return self.set_status_save_progress(phantom.APP_SUCCESS, CISCOISE_SUCC_TEST_CONNECTIVITY_PASSED)
        else:
            return self.set_status_save_progress(
                phantom.APP_ERROR,
                CISCOISE_ERR_TEST_CONNECTIVITY_FAILED_ERR_CODE,
                code=resp.status_code
            )

    def _test_connectivity(self, param):

        config = self.get_config()
        verify = config[phantom.APP_JSON_VERIFY]
        self.save_progress("Connecting to first device")
        result = self._test_connectivity_to_device(self._base_url, verify)

        if self._ha_device:
            self.save_progress("Connecting to second device")
            result = self._test_connectivity_to_device(self._ha_device_url, verify)

        return result

    def handle_action(self, param):

        result = None
        action = self.get_action_identifier()

        if action == phantom.ACTION_ID_TEST_ASSET_CONNECTIVITY:
            result = self._test_connectivity(param)
        elif action == self.ACTION_ID_LIST_SESSIONS:
            result = self._list_sessions(param)
        elif action == self.ACTION_ID_TERMINATE_SESSION:
            result = self._terminate_session(param)
        elif action == self.ACTION_ID_LOGOFF_SYSTEM:
            result = self._logoff_system(param)
        elif action == self.ACTION_ID_QUARANTINE_SYSTEM:
            result = self._quarantine_system(param)
        elif action == self.ACTION_ID_UNQUARANTINE_SYSTEM:
            result = self._unquarantine_system(param)
        elif action == self.ACTION_ID_LIST_ENDPOINTS:
            result = self._list_endpoints(param)
        elif action == self.ACTION_ID_GET_ENDPOINT:
            result = self._get_endpoint(param)
        elif action == self.ACTION_ID_UPDATE_ENDPOINT:
            result = self._update_endpoint(param)
        elif action == self.ACTION_ID_LIST_RESOURCES:
            result = self._list_resources(param)
        elif action == self.ACTION_ID_GET_RESOURCES:
            result = self._get_resources(param)
        elif action == self.ACTION_ID_DELETE_RESOURCE:
            result = self._delete_resource(param)
        elif action == self.ACTION_ID_CREATE_RESOURCE:
            result = self._create_resource(param)
        elif action == self.ACTION_ID_UPDATE_RESOURCE:
            result = self._update_resource(param)
        elif action == self.ACTION_ID_APPLY_POLICY:
            result = self._apply_policy(param)
        elif action == self.ACTION_ID_CLEAR_POLICY:
            result = self._clear_policy(param)
        elif action == self.ACTION_ID_LIST_POLICIES:
            result = self._list_policies(param)
        elif action == self.ACTION_ID_CREATE_POLICY:
            result = self._create_policy(param)
        elif action == self.ACTION_ID_DELETE_POLICY:
            result = self._delete_policy(param)

        return result


if __name__ == "__main__":

    import sys

    import pudb

    pudb.set_trace()

    if len(sys.argv) < 2:
        print("No test json specified as input")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        in_json = f.read()
        in_json = json.loads(in_json)
        print(json.dumps(in_json, indent=4))

        connector = CiscoISEConnector()
        connector.print_progress_message = True
        ret_val = connector._handle_action(json.dumps(in_json), None)
        print(json.dumps(json.loads(ret_val), indent=4))

    sys.exit(0)
