# KOPF Edge Appliance Operator

## Running locally on your laptop

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
kopf run main.py --verbose
```

## Running Ansible inside the operator

[ansible runner](https://ansible.readthedocs.io/projects/runner/en/latest/python_interface/)