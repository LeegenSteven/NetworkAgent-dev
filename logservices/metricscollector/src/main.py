import time
import itertools
import json
import os
import datetime
from google.api import metric_pb2 as ga_metric
from google.cloud import monitoring_v3
from google.cloud import spanner

# Adapt logging strategy if we run on GCP
if os.environ.get("CLOUD_RUN_WORKER_POOL"):
  # Attach the Cloud Logging handler to the Python root logger 
  # by calling the setup_logging method. By doing so Cloud Logging
  # will properly report the logs severity for instance. If we do it
  # directly (as above) all logs are classified with ERROR severity
  # (see https://cloud.google.com/logging/docs/setup/python)
  import google.cloud.logging
  logging_client = google.cloud.logging.Client()
  import logging
  logging_client.setup_logging(log_level=logging.DEBUG)

  logger = logging.getLogger(__name__)

  # After importing the Python standard logging library we end up with 2 log
  # handlers at the root level causing duplicate log entries to appear
  # in Cloud Logging, one that comes from the Cloud Logging Structured
  # handler and the other from the standard Python StreamHandler
  # Logger root handlers: [<StreamHandler <stderr> (NOTSET)>, <StructuredLogHandler <stderr> (NOTSET)>]
  # Remove the standard Python logging handler to avoid duplicate (first handler in the list)
  del logging.getLogger().handlers[0]
else:
  import logging
  logging.basicConfig(level=logging.DEBUG)
  logger = logging.getLogger(__name__)

# Configuration
PROJECT_ID = os.environ.get("GOOGLE_PROJECT")
INSTANCE_ID = os.environ.get("GOOGLE_SPANNER_INSTANCE")
DATABASE_ID = os.environ.get("GOOGLE_SPANNER_DATABASE")

# Must be greater than the ops agent scrape interval so that we always
# get the same number of data points at each polling interval
# see ops agent config file for more details
# (operator/src/vyosvm/playbooks/templates/ops-agent-config.yaml.j2)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 20))

SELECTED_METRICS = [
  # SYSTEM metrics
  'prometheus.googleapis.com/node_load1/gauge',
  'prometheus.googleapis.com/node_memory_SwapFree_bytes/gauge',
  'prometheus.googleapis.com/node_memory_MemTotal_bytes/gauge',
  'prometheus.googleapis.com/node_network_up/gauge',
  'prometheus.googleapis.com/node_network_carrier/gauge',
  'prometheus.googleapis.com/node_network_carrier_changes_total/counter',
  'prometheus.googleapis.com/node_network_receive_bytes_total/counter',
  'prometheus.googleapis.com/node_network_receive_drop_total/counter',
  'prometheus.googleapis.com/node_network_receive_errs_total/counter',
  'prometheus.googleapis.com/node_network_receive_packets_total/counter',
  'prometheus.googleapis.com/node_network_transmit_bytes_total/counter',
  'prometheus.googleapis.com/node_network_transmit_drop_total/counter',
  'prometheus.googleapis.com/node_network_transmit_errs_total/counter',
  'prometheus.googleapis.com/node_network_transmit_packets_total/counter',
  # ROUTING metrics
  'prometheus.googleapis.com/frr_bfd_peer_count/gauge',
  'prometheus.googleapis.com/frr_collector_up/gauge',
  'prometheus.googleapis.com/frr_ospf_neighbor_adjacencies/gauge',
  'prometheus.googleapis.com/frr_ospf_neighbors/gauge',
  'prometheus.googleapis.com/frr_route_total/gauge',
  'prometheus.googleapis.com/frr_route_total_fib/gauge',
  'prometheus.googleapis.com/process_open_fds/gauge',
  'prometheus.googleapis.com/process_network_receive_bytes_total/counter',
  'prometheus.googleapis.com/process_network_transmit_bytes_total/counter',
]

def get_metric_aggregation(descriptor, window_seconds=60):
    """
    Determines the correct aligner based on the metric's Kind and Value Type.
    """
    # 1. Fetch the Metric Descriptor to see what "Kind" it is
    kind = descriptor.metric_kind  # GAUGE, CUMULATIVE, or DELTA
    value_type = descriptor.value_type  # DOUBLE, INT64, etc.

    # 2. Logic to choose the Aligner
    if kind == descriptor.MetricKind.CUMULATIVE:
        # Counters (like node_forks_total) need a rate or delta
        # Here we use the rate which the delta divided by the time interval
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_RATE
        
    elif kind == descriptor.MetricKind.GAUGE:
        # Gauges (like CPU/RAM) can be averaged
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_MEAN
    else:
        # Fallback for DELTA or unknown types
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_NEXT_OLDER

    return monitoring_v3.Aggregation({
        "alignment_period": {"seconds": window_seconds},
        "per_series_aligner": aligner,
        "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_NONE,
    })


def fetch_all_vyos_metrics(client, project_name, start_time):
    """Fetches every metric belonging to the VyOS job."""

    # Add a lookback"safety offset" to ensure the data has 
    # actually arrived in GCP before you ask for it
    offset = 2
    now_timestamp = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - offset
    start_timestamp = now_timestamp - POLL_INTERVAL
    
    interval = monitoring_v3.TimeInterval({
        "end_time": {"seconds": now_timestamp},
        "start_time": {"seconds": start_timestamp},
    })

    # 1. Discover all metric types matching prefix
    # Need to list DESCRIPTORS first because list_time_series requires exact metric.type match
    # LJ for some reason I couldn't use a filter
    # argument in the list_metric_descriptors call below.abs
    # descriptor_filter = 'metric.type = starts_with("prometheus/")'

  # 1. Alternate approach: use a list of selected metrics instead of listing all descriptors
    descriptor_pages = [
        client.get_metric_descriptor(name=f"projects/{PROJECT_ID}/metricDescriptors/{metric}")
        for metric in SELECTED_METRICS
    ]
    
    generators = []
    
    for descriptor in descriptor_pages:
        # Skip non-prometheus metrics (I couldn't make the filter argument
        # work properly in the list metric descriptors)
        # and also the list of labels (list of LabelDescriptor) must contain
        # a label with key equals to "router_name"
        #
        # NOTE: this test is not needed when using the list of selected metrics
        # but as it's good to be paranoid, we keep it :-)
        has_router_name_label = any(label.key == "router_name" for label in descriptor.labels)
        if not descriptor.type.startswith("prometheus.googleapis.com/") or not has_router_name_label:
            continue
        #logger.debug(f"Fetching series for: {descriptor.type}\nFull descriptor: {descriptor}")
        
        # Get the proper aggregation method for this metric
        aggregation = get_metric_aggregation(descriptor, POLL_INTERVAL)
        
        # 2. Fetch time series for each specific metric type
        metric_filter = f"metric.type = \"{descriptor.type}\""
        try:
            results = client.list_time_series(request={
                "name": project_name,
                "filter": metric_filter,
                "interval": interval,
                "aggregation": aggregation,
            })
            logger.debug(f"Results count for {descriptor.type}: {len(list(results))}")
            generators.append(results)
        except Exception as e:
            logger.error(f"Error fetching {descriptor.type}: {e}")

    # Chain all the individual iterators into one stream
    return itertools.chain.from_iterable(generators)

def run_worker():
    mon_client = monitoring_v3.MetricServiceClient()
    span_client = spanner.Client(project=PROJECT_ID, disable_builtin_metrics=True)
    db = span_client.instance(INSTANCE_ID).database(DATABASE_ID)
    project_name = f"projects/{PROJECT_ID}"
    
    logger.info(f"Worker Pool started. Polling all VyOS metrics every {POLL_INTERVAL}s...")
    last_poll_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=POLL_INTERVAL)

    while True:
      current_poll_time = datetime.datetime.now(datetime.timezone.utc)
      data_to_insert = []

      try:
        series_data = fetch_all_vyos_metrics(mon_client, project_name, last_poll_time)
        
        for series in series_data:
          #logger.debug(f"============================\nSeries metric labels: {series.metric.labels}")
          
          # Skip all series not coming from a vyos router
          router_name = series.metric.labels.get("router_name")
          job_name = series.resource.labels.get("job")
          if not router_name or job_name != "vyos-lab":
            continue

          # log the series data structure
          logger.debug(f"Processing series for: {series.metric.type}")
          
          # 1. Extract name and type from 'prometheus.googleapis.com/metric_name/type'
          parts = series.metric.type.split('/')
          raw_metric_name = parts[-2]
          prom_type = parts[-1] # Extracts gauge, counter, summary, etc.
          
          # 2. Categorize
          category = "SYSTEM" if raw_metric_name.startswith("node_") else "ROUTING"

          # 3. Identify Node and Interface
          iface = series.metric.labels.get("device")
          
          logger.debug(f"Category: {category}, Node: {router_name}, Interface: {iface}")
          
          for point in series.points:
              val = point.value.double_value if point.value.double_value else float(point.value.int64_value)
              logger.debug(f"point value: {val}")
              data_to_insert.append((
                point.interval.end_time,
                router_name,
                raw_metric_name,
                prom_type,
                category,
                val,
                json.dumps(dict(series.metric.labels)),
                iface
              ))
      except Exception as e:
          logger.error(f"Error during API poll: {e}")

      # Batch write to Spanner
      if data_to_insert:
        #logger.debug(f"Data to insert: {data_to_insert}")
        try:
            with db.batch() as batch:
                logger.info(f"Inserting {len(data_to_insert)} metric points at {current_poll_time}")
                batch.insert(
                    table="NetworkMetrics",
                    columns=("timestamp", "node_name", "metric_name", "metric_type", "kind", "value", "labels", "interface"),
                    values=data_to_insert
                )
        except Exception as e:
            logger.error(f"Error writing to Spanner: {e}")

      last_poll_time = current_poll_time
      logger.debug(f"Sleeping for {POLL_INTERVAL} seconds")
      time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run_worker()


# Documentation
# here is a metric descriptor sample
"""
name: "projects/networkagent-434609/metricDescriptors/prometheus.googleapis.com/frr_bfd_peer_count/gauge"
labels {
  key: "otel_scope_version"
}
labels {
  key: "router_name"
}
labels {
  key: "otel_scope_name"
}
labels {
  key: "instance_name"
}
labels {
  key: "machine_type"
}
metric_kind: GAUGE
value_type: DOUBLE
description: "Number of peers detected."
type: "prometheus.googleapis.com/frr_bfd_peer_count/gauge"
monitored_resource_types: "prometheus_target"
"""

# here is a series sample:
"""
metric {
  labels {
    key: "router_name"
    value: "p1"
  }
  labels {
    key: "otel_scope_version"
    value: "v0.138.0"
  }
  labels {
    key: "otel_scope_name"
    value: "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/prometheusreceiver"
  }
  labels {
    key: "machine_type"
    value: "projects/784219571708/machineTypes/e2-standard-16"
  }
  labels {
    key: "instance_name"
    value: "networkvm"
  }
  type: "prometheus.googleapis.com/frr_bfd_peer_count/gauge"
}
resource {
  type: "prometheus_target"
  labels {
    key: "project_id"
    value: "networkagent-434609"
  }
  labels {
    key: "namespace"
    value: "3515116987487830921/networkvm"
  }
  labels {
    key: "location"
    value: "europe-west1-c"
  }
  labels {
    key: "job"
    value: "vyos-lab"
  }
  labels {
    key: "instance"
    value: "192.168.122.11:9101"
  }
  labels {
    key: "cluster"
    value: "__gce__"
  }
}
metric_kind: GAUGE
value_type: DOUBLE
points {
  interval {
    start_time {
      seconds: 1771312114
      nanos: 864000000
    }
    end_time {
      seconds: 1771312114
      nanos: 864000000
    }
  }
  value {
    double_value: 0
  }
}
points {
  interval {
    start_time {
      seconds: 1771312099
      nanos: 864000000
    }
    end_time {
      seconds: 1771312099
      nanos: 864000000
    }
  }
  value {
    double_value: 0
  }
}
points {
  interval {
    start_time {
      seconds: 1771312084
      nanos: 864000000
    }
    end_time {
      seconds: 1771312084
      nanos: 864000000
    }
  }
  value {
    double_value: 0
  }
}
points {
  interval {
    start_time {
      seconds: 1771312069
      nanos: 864000000
    }
    end_time {
      seconds: 1771312069
      nanos: 864000000
    }
  }
  value {
    double_value: 0
  }
}

"""