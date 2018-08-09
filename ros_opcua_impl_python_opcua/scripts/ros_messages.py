from opcua.common.instantiate import instantiate

from ros_global import *
from ros_opc_ua import *

ROS_BUILD_IN_DATA_TYPES = {'bool': ua.VariantType.Boolean,
                           'int8': ua.VariantType.SByte,
                           'byte': ua.VariantType.SByte,  # deprecated int8
                           'uint8': ua.VariantType.Byte,
                           'char': ua.VariantType.Byte,  # deprecated uint8
                           'int16': ua.VariantType.Int16,
                           'uint16': ua.VariantType.UInt16,
                           'int32': ua.VariantType.Int32,
                           'uint32': ua.VariantType.UInt32,
                           'int64': ua.VariantType.Int64,
                           'uint64': ua.VariantType.UInt64,
                           'float32': ua.VariantType.Float,
                           'float64': ua.VariantType.Float,
                           'string': ua.VariantType.String,
                           'time': ua.VariantType.DateTime,
                           'duration': ua.VariantType.DateTime}


def extract_array_info(type_str):
    """ROS only support 1 dimensional array"""
    is_array = False
    if '[' in type_str and type_str[-1] == ']':
        type_str = type_str.split('[', 1)[0]
        is_array = True

    return type_str, is_array


class OpcUaROSMessage:
    def __init__(self, server, idx, idx_name):
        self._server = server
        self._idx = idx

        self._created_data_types = {}

        self._dict_builder = DataTypeDictionaryBuilder(server, idx, 'ROSDictionary')
        self._type_dictionary = OPCTypeDictionaryBuilder(idx_name, ROS_BUILD_IN_DATA_TYPES)

    def _is_new_type(self, message):
        return message not in ROS_BUILD_IN_DATA_TYPES and message not in self._created_data_types

    def _create_data_type(self, type_name):
        new_dt_id = self._dict_builder.create_data_type(to_camel_case(type_name))
        self._created_data_types[type_name] = new_dt_id
        self._type_dictionary.append_struct(type_name)

    def _recursively_create_message(self, msg):
        if self._is_new_type(msg):
            self._create_data_type(msg)
        message = get_message_class(msg)
        if not message:  # Broken packages
            return
        for variable_type, data_type in zip(message.__slots__, getattr(message, '_slot_types')):
            base_type_str, is_array = extract_array_info(data_type)
            if self._is_new_type(base_type_str):
                self._create_data_type(base_type_str)
                self._recursively_create_message(base_type_str)

            self._type_dictionary.add_field(base_type_str, variable_type, msg, is_array)

    def _create_messages(self):
        messages = get_ros_messages()
        for msg in messages:
            if msg not in self._created_data_types:
                self._recursively_create_message(msg)

    def _process_service_classes(self, srv):
        msg_name = getattr(srv, '_type')
        self._create_data_type(msg_name)
        for variable_type, data_type in zip(srv.__slots__, getattr(srv, '_slot_types')):
            base_type_str, is_array = extract_array_info(data_type)
            self._type_dictionary.add_field(base_type_str, variable_type, msg_name, is_array)

    def _create_services(self):
        """since srv can not embed another .srv, no recursion is needed"""
        services = get_ros_services()
        for srv in services:
            service = get_service_class(srv)
            if not service:  # Broken packages
                continue
            self._process_service_classes(getattr(service, '_request_class'))
            self._process_service_classes(getattr(service, '_response_class'))

    def create_ros_data_types(self):
        self._create_messages()
        self._create_services()

        ros_opc_model = self._type_dictionary.get_dict_value()
        self._dict_builder.set_dict_byte_string(ros_opc_model)

        return self._created_data_types


def update_node_with_message(node_name, message, idx):
    """
    the method update all variable of a node or copy all values from message to node
    IMPORT: the variable browser name of the variable node must be the same as the attribute of the message object
    """
    value = message
    # set value if exists
    if value is not None:
        if not (hasattr(value, '__slots__') or type(value) in (list, tuple)):  # PRIMITIVE TYPE
            node_name.set_value(value)
        elif type(value) in (list, tuple):  # handle array
            if len(value) > 0:
                node_name.set_value(value)
        else:  # complex type
            if type(message).__name__ in ('Time', 'Duration'):
                value = message.secs
                node_name.set_value(value)
                # node.set_value(str(value))
            else:
                node_name.set_value(str(value))

    node_children = node_name.get_children()
    for child in node_children:
        # if child a variable
        if child.get_node_class() == ua.NodeClass.Variable:
            # get attr_name
            if hasattr(value, child.get_browse_name().Name):
                update_node_with_message(child,  getattr(value, child.get_browse_name().Name), idx)


def instantiate_customized(parent, node_type, node_id=None, bname=None, idx=0):
    """
    Please take care that in the new version of python opcua, the dname, ie. the DisplayName is deleted from the
     parameter list in instantiate function
    :param parent: 
    :param node_type: 
    :param node_id: 
    :param bname: BrowseName
    :param idx: 
    :return: 
    """
    nodes = instantiate(parent, node_type, nodeid=node_id, bname=bname, idx=idx)
    new_node = nodes[0]
    _init_node_recursively(new_node, idx)
    return new_node


def _init_node_recursively(node_name, idx):
    """ 
    This function initiate all the sub variable with complex type of customized type of the node
    TODO: the instantiate function itself is a recursive realization, try to use the original one.
    :param node_name: opc ua node
    :param idx:
    """
    children = node_name.get_children()
    if not (len(children) > 0):
        return
    for child in children:
        if _is_variable_and_string_type(child):
            variable_type_name = child.get_type_definition().Identifier.replace('Type', '')
            if variable_type_name in messageNode.keys():
                variable_type = messageNode[variable_type_name]
                created_node = instantiate(node_name, variable_type, bname=child.get_browse_name(), idx=idx)[0]
                _init_node_recursively(created_node, idx)
                child.delete()


def _is_variable_and_string_type(node_name):
    return node_name.get_node_class() == ua.NodeClass.Variable and \
           node_name.get_type_definition().NodeIdType == ua.NodeIdType.String  # complex type or customized type


def update_message_instance_with_node(message, node_name):
    """ the function try to update all message attribute with the help of node info.
    NB: all node variable browse name must be the same name as the the message attribute """
    variables = node_name.get_children(nodeclassmask=ua.NodeClass.Variable)

    if len(variables) > 0:
        for var in variables:
            attr_name = var.get_browse_name().Name
            if hasattr(message, attr_name):
                if len(var.get_children(nodeclassmask=ua.NodeClass.Variable)) == 0:  # primitive type
                    setattr(message, attr_name, correct_type(var, type(getattr(message, attr_name))))
                    # var.get_value())
                    # setattr(message, attr_name, var.get_value())
                else:     # complex type
                    update_message_instance_with_node(getattr(message, attr_name), var)

    return message
    # if has variables recursion
    # else update value
