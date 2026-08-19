import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument("paths",nargs="+");a=p.parse_args();print(json.dumps([{"path":x,"bytes":Path(x).stat().st_size,"sha256":hashlib.sha256(Path(x).read_bytes()).hexdigest()} for x in a.paths],indent=2))
