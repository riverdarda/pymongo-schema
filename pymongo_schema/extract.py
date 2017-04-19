# coding: utf8
""" 
Functions in this library which take a 'schema' as argument, modify this schema as a side-effect and have no value.

Schema are hierarchically nested : (see also README.md)
- A MongoDB instance contains databases
    {
        "database_name_1": database_schema_1,
        "database_name_2": database_schema_2            
    }
- A database contains collections
    {
        "collection_name_1": collection_schema_1,
        "collection_name_2": collection_schema_2
    }

- A collection maintains a 'count' and contains 1 object
    { 
        "count" : int, 
        "object": object_schema 
    }

- An object contains fields.
Objects are initialized as defaultdict(empty_field_schema) to simplify the code 
    { 
        "field_name_1" : field_schema_1, 
        "field_name_2": field_schema_2 
    }

- A field maintains 'count' and 'types_count' information, and has optional 'array_types_count' and 'object'
    {
        'count': int,
        'type', 'type_str',
        'types_count': defaultdict(int) # count for each encountered type  
        'array_type', 'type_str', # (optional if array)
        'array_types_count': defaultdict(int), # (optional if array) count for each type encountered  in arrays
        'object': {}, # (optional if object) object_schema 
    } 
"""

from collections import defaultdict
from mongo_sql_types import get_type_string, common_parent_type
import logging
logger = logging.getLogger(__name__)


def extract_pymongo_client_schema(pymongo_client, database_names=None, collection_names=None):
    """ Extract the schema for every database in database_names
    
    :param pymongo_client: pymongo.mongo_client.MongoClient
    :param database_names: str, list of str, default None
    :param collection_names: str, list of str, default None
        Will be used for every database in database_names list
    :return mongo_schema: dict
    """

    if isinstance(database_names, basestring):
        database_names = [database_names]

    if database_names is None:
        database_names = pymongo_client.database_names()
        database_names.remove('admin')
        database_names.remove('local')

    mongo_schema = dict()
    for database in database_names:
        logger.info('Extract schema of database ' + database)
        pymongo_database = pymongo_client[database]
        mongo_schema[database] = extract_database_schema(pymongo_database, collection_names)

    return mongo_schema


def extract_database_schema(pymongo_database, collection_names=None):
    """ Extract the database schema, for every collection in collection_names

    :param pymongo_database: pymongo.database.Database
    :param collection_names: str, list of str, default None
    :return database_schema: dict
    """
    if isinstance(collection_names, basestring):
        collection_names = [collection_names]

    if collection_names is None:
        collection_names = pymongo_database.collection_names()

    database_schema = dict()
    for collection in collection_names:
        logger.info('...collection ' + collection)
        pymongo_collection = pymongo_database[collection]
        database_schema[collection] = extract_collection_schema(pymongo_collection)

    return database_schema


def extract_collection_schema(pymongo_collection):
    """ Iterate through all document of a collection to create its schema

    - Init collection schema
    - Add every document from MongoDB collection to the schema
    - Post-process schema

    :param pymongo_collection: pymongo.collection.Collection
    :return collection_schema: dict
    """
    collection_schema = {
        'count': 0,
        "object": init_empty_object_schema()
    }

    n = pymongo_collection.count()
    i = 0
    for document in pymongo_collection.find({}):
        collection_schema['count'] += 1
        add_document_to_object_schema(document, collection_schema['object'])
        i += 1
        if i % 10**5 == 0 or i == n:
            logger.info('   scanned {} documents out of {} ({:.2f} %)'.format(i, n, (100. * i)/n))

    post_process_schema(collection_schema)
    collection_schema = recursive_default_to_regular_dict(collection_schema)
    return collection_schema


def recursive_default_to_regular_dict(value):
    """ If value is a dictionnary, recursively replace defaultdict to regular dict 
    
    Note : defaultdict are instances of dict
    
    :param value: 
    :return d: dict or original value
    """
    if isinstance(value, dict):
        d = {k: recursive_default_to_regular_dict(v) for k, v in value.iteritems()}
        return d
    else:
        return value


def post_process_schema(object_count_schema):
    """ Clean and add information to schema once it has been built

    - compute the main type for each field 
    - compute the proportion of non null values in the parent object
    - recursively postprocess imbricated object schemas

    :param object_count_schema: dict
    This schema can either be a field_schema or a collection_schema
    """
    object_count = object_count_schema['count']
    object_schema = object_count_schema['object']
    for field_schema in object_schema.values():

        summarize_types(field_schema)
        field_schema['prop_in_object'] = round((field_schema['count']) / float(object_count), 5)

        if 'object' in field_schema:
            post_process_schema(field_schema)


def summarize_types(field_schema):
    """ Summarize types information to one 'type' field 
    
    Add a 'type' field, compatible with all encountered types in 'types_count'. 
    This is done by taking the least common parent type between types.
    
    If 'ARRAY' type count is not null, the main type is 'ARRAY'. 
    An 'array_type' is defined, as the least common parent type between 'types' and 'array_types'
    
    :param field_schema:    
    """

    type_list = field_schema['types_count'].keys()
    type_list += field_schema.get('array_types_count', {}).keys()  # Only exists if 'ARRAY' in 'types_count'

    cleaned_type_list = [type_name for type_name in type_list if type_name != 'ARRAY' and type_name != 'null']
    common_type = common_parent_type(cleaned_type_list)

    if 'ARRAY' in field_schema['types_count']:
        field_schema['type'] = 'ARRAY'
        field_schema['array_type'] = common_type
    else:
        field_schema['type'] = common_type


def init_empty_object_schema():
    """ Generate an empty object schema.

    We use a defaultdict of empty fields schema. This avoid to test for the presence of fields.
    :return: defaultdict(empty_field_schema)
    """

    def empty_field_schema():
        field_dict = {
            'types_count': defaultdict(int),
            'count': 0,
        }
        return field_dict

    empty_object = defaultdict(empty_field_schema)
    return empty_object


def add_document_to_object_schema(document, object_schema):
    """ Add a all fields of a document to a local object_schema.

    :param document: dict
    contains a MongoDB Object
    :param object_schema: dict
    """
    for field, value in document.iteritems():
        add_value_to_field_schema(value, object_schema[field])


def add_value_to_field_schema(value, field_schema):
    """ Add a value to a field_schema

    - Update count or 'null_count' count.
    - Define or check the type of value.
    - Recursively add 'list' and 'dict' value to the schema.

    :param value:
    value corresponding to a field in a MongoDB Object
    :param field_schema: dict
    subdictionnary of the global schema dict corresponding to a field
    """
    field_schema['count'] += 1
    add_value_type(value, field_schema)
    add_potential_list_to_field_schema(value, field_schema)
    add_potential_document_to_field_schema(value, field_schema)


def add_potential_document_to_field_schema(document, field_schema):
    """ Add a document to a field_schema
    
    - Exit if document is not a dict
    
    :param document: dict (or skipped)
    :param field_schema: 
    """
    if isinstance(document, dict):
        if 'object' not in field_schema:
            field_schema['object'] = init_empty_object_schema()
        add_document_to_object_schema(document, field_schema['object'])


def add_potential_list_to_field_schema(value_list, field_schema):
    """ Add a list of values to a field_schema

    - Exit if value_list is not a list
    - Define or check the type of each value of the list.
    - Recursively add 'dict' values to the schema.   

    :param value_list: list (or skipped) 
    :param field_schema: dict
    """
    if isinstance(value_list, list):
        if 'array_types_count' not in field_schema:
            field_schema['array_types_count'] = defaultdict(int)

        if not value_list:
            add_value_type(None, field_schema, type_str='array_types_count')

        for value in value_list:
            add_value_type(value, field_schema, type_str='array_types_count')
            add_potential_document_to_field_schema(value, field_schema)


def add_value_type(value, field_schema, type_str='types_count'):
    """ Define the type_str in field_schema, or check it is equal to the one previously defined. 

    :param value: 
    :param field_schema: dict
    :param type_str: str, either 'types_count' or 'array_types_count'
    
    """
    value_type_str = get_type_string(value)
    field_schema[type_str][value_type_str] += 1
