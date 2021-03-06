"""Import GIS dataset resources into CKAN

Usage:
    geoimport (-g GIS_REPO)
        (-m MAPPING)
        (-r CKAN_URL)
        (-a APIKEY)
        [-l LOG]
        [-d DATASET]
        [--force]
        [--dry-run]

Options:
    -g --gis                Location of the GIS resource repo to import from
    -m --mapping            Location of the YAML file where resource mappings are stored
    -r --remote             CKAN URL
    -a --apikey             CKAN API Key to use when importing
    -d --dataset            If you want to import a single dataset
    -l LOG --logfile LOG    Path to where you'd like the logs to be output [default: gis_import.log]
    --dry-run               Show what would be imported but don't actually import
    --force                 Update resource whether it needs updating or not      
"""

import os
import zipfile
import logging
import yaml
import ckanapi
import requests

from docopt import docopt
from datetime import datetime
from dateutil.parser import parse

arguments = docopt(__doc__)

stream = open(arguments['MAPPING'], 'r')
mapping =  yaml.load(stream)

single_dataset = arguments['DATASET']

ckan = ckanapi.RemoteCKAN(arguments['CKAN_URL'], apikey=arguments['APIKEY'])
base_import_url = arguments['GIS_REPO']
force = arguments['--force']
debug = arguments['--dry-run']

logging.basicConfig(filename=arguments['--logfile'], level=logging.INFO)

def download_temp_file(resource_path, file_name):
        r = requests.get(resource_path, stream=True)
        if r.status_code == 200:
            if r.headers['content-type'] == 'text/xml':
                with open(file_name, 'w') as f:
                    f.write(r.content)
                    f.close()
                return True
            else:
                with open(file_name, 'wb') as f:
                    for chunk in r.iter_content():
                        f.write(chunk)
                    f.close()
                return True
        else:
            return False

def upload_file_to_resource(resource, file_object):
    filename = "{0}.{1}".format(resource['name'], resource['format'])
    if resource['format'].lower() == 'shp':
        filename = "{0}.{1}.zip".format(resource['name'], resource['format'])
    resource['upload'] = (filename, file_object)
    resource['last_modified'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
    
    try:
        ckan.action.resource_update(**resource)
    except ckanapi.errors.CKANAPIError as e:
        logging.error(e.message)

def import_shapefile(resource, mapping):
    shape_destination_dir = os.path.join('temp_data', resource['name'] + '_shp')
    if not os.path.exists(shape_destination_dir):
        os.makedirs(shape_destination_dir)

    for shape_format, shape_location in mapping['shp'].iteritems():
        resource_location = base_import_url + shape_location
        file_name = "{0}.{1}".format(resource['name'], shape_format)
        download_location = os.path.join(shape_destination_dir, file_name)
        download_temp_file(resource_location, download_location)

    zip_filename = os.path.join('temp_data', resource['name'] + '.shp.zip')
    zip = zipfile.ZipFile(zip_filename, 'w')
    for root, dirs, files in os.walk(shape_destination_dir):
        for file in files:
            zip.write(os.path.join(root, file), file)
    zip.close()


    upload_file_to_resource(resource, open(zip_filename))


def get_import_url(resource, mapping):
    afile = mapping[resource['format'].lower()]

    if resource['format'].lower() == 'shp':
        afile = mapping[resource['format'].lower()]['shp']

    import_file = "{0}{1}".format(base_import_url, afile)

    return import_file

def get_import_file_date(import_file):
    head = requests.head(import_file)

    if head.status_code == 404:
        logging.warning("Resource is missing: {0}".format(import_file))
        return None

    try:
        date_modified = parse(head.headers['last-modified']).replace(tzinfo=None)
    except KeyError as e:
        logging.error("Could not determine last modified date of {0}".format(import_file))

    return date_modified

def out_of_date(resource, import_file):
    date_modified = get_import_file_date(import_file)

    #Date modified is usually None when the file is missing from GIS repo
    if date_modified is None:
        return False

    if resource['last_modified']:
        resource_modified = parse(resource['last_modified']).replace(tzinfo=None)
    elif resource['created']:
        resource_modified = parse(resource['created']).replace(tzinfo=None)
    else:
        resource_modified = datetime.now()

    if (date_modified > resource_modified) or force:
        return True
    else:
        return False

def perform_import(resource, mapping):
    logging.info("updating resource {0}:{1}".format(resource['name'], resource['format']))

    if resource['format'].lower() == 'shp':
        import_shapefile(resource, mapping)
    else:
        url = get_import_url(resource, mapping)
        f = requests.get(url, stream=True)
        upload_file_to_resource(resource, f.raw)
        f.close()

def main_job(dataset, resources_mapping):
    try:
        package = ckan.action.package_show(id=dataset)
    except ckanapi.errors.NotFound:
        logging.error("dataset not found: {0}".format(dataset))
        return

    existing_formats = [res['format'].lower() for res in package['resources']]
    for resource_format in resources_mapping:
        if resource_format not in existing_formats:
            resource = ckan.action.resource_create(
                package_id=package['id'],
                url='http://example.com',
                format=resource_format,
                last_modified=datetime.now().isoformat(),
                name="{0}:{1}".format(package['name'], resource_format)
            )
        else:
            resource = [res for res
                in package['resources']
                if res['format'].lower() == resource_format
                ][0]

        import_file = get_import_url(resource, resources_mapping)
        if out_of_date(resource, import_file):
            if debug:
                logging.info("Would refresh {0}".format(resource['url']))
            else:
                perform_import(resource, resources_mapping)

if single_dataset and mapping[single_dataset]:
    main_job(single_dataset, mapping[single_dataset])

else:
    for dataset, resources_mapping in mapping.iteritems():
        main_job(dataset, resources_mapping)
