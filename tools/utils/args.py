import utils.constants as constants
import argparse

def parseargs():
    parser = argparse.ArgumentParser(description='Network Connectivity Tools.')
    parser.add_argument('--port', default='80', help='port to run server on')
    parser.add_argument('--credentials', default='/tools.json', help='location of the credentials file')
    parser.add_argument('--project', default='free5gc-384814', help='name of the GCP project')
    parser.add_argument('--cluster', default='networkautomation', help='name of the GKE cluster')
    parser.add_argument('--zone', default='europe-west2-a', help='zone cluster is deployed to')

    args = parser.parse_args()

    constants.PORT=args.port
    constants.SERVICE_FILE_LOCATION=args.credentials
    constants.CLUSTER=args.cluster
    constants.PROJECT=args.project
    constants.ZONE=args.zone