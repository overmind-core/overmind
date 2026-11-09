import argparse
import base64
import json
import shlex
from pathlib import Path

from daytona import Daytona, DaytonaConfig
from dotenv import load_dotenv

parser = argparse.ArgumentParser()
parser.add_argument("case")
parser.add_argument("--sandbox", required=True)
parser.add_argument("--upgrade-runtime", action="store_true")
args = parser.parse_args()
directory = Path("output/workshop-benchmark") / args.case
state = json.loads((directory / "state.json").read_text())
attempt = state["attempts"][-1]
load_dotenv(Path.home() / ".config/overmind/workshop.env")
sandbox = Daytona(DaytonaConfig(api_url="https://app.daytona.io/api", otel_enabled=False)).get(
    args.sandbox
)
code = """import json,shutil,zipfile,hashlib
from pathlib import Path
from uuid import UUID,uuid5
from workshop_runtime.replay import replay
from workshop_runtime.environment import identity
root=Path('/home/daytona/overmind')/TASK
old=root/'.agent/replays'/REPLAY
prior=json.loads((old/'receipt.json').read_text())
assert hashlib.sha256((old/'package.zip').read_bytes()).hexdigest()==prior['package_sha256']
package=root/'work/benchmark-retained.zip'
suffix='independent-benchmark-replay'
if UPGRADE:
    package=root/'work/benchmark-upgraded.zip'
    with zipfile.ZipFile(old/'package.zip') as previous, zipfile.ZipFile(package,'w') as updated:
        for name in previous.namelist():
            payload=previous.read(name)
            if name=='manifest.json':
                manifest=json.loads(payload)
                manifest['runtime']=identity()
                payload=json.dumps(manifest).encode()
            updated.writestr(name,payload)
    suffix+=':'+identity()
else:
    shutil.copyfile(old/'package.zip',package)
result=replay(root,str(uuid5(UUID(TASK),suffix)),package.name,parameters=prior['parameters'],partition=prior.get('partition_recipe'))
assert result['state']=='completed',result
assert result['source_sha256']==prior['source_sha256']
assert [s['output_sha256'] for s in result['steps']]==[s['output_sha256'] for s in prior['steps']]
assert result.get('assignments_sha256')==prior.get('assignments_sha256')
print(json.dumps({'passed':True,'rows':result['output_rows'],'runtime':result['runtime'],'package_sha256':result['package_sha256'],'original_package_sha256':prior['package_sha256'],'source_sha256':result['source_sha256'],'all_step_output_hashes_match':True,'assignments_match':True,'only_runtime_pin_changed':UPGRADE,'model_calls':0}))
"""
code = (
    "TASK="
    + repr(attempt["task"])
    + "\nREPLAY="
    + repr(attempt["publication"]["replay"])
    + "\nUPGRADE="
    + repr(args.upgrade_runtime)
    + "\n"
    + code
)
payload = base64.b64encode(code.encode()).decode()
r = sandbox.process.exec(
    "/opt/overmind-runtime/venv/bin/python -c "
    + shlex.quote("import base64;exec(base64.b64decode(" + repr(payload) + "))"),
    timeout=240,
)
if r.exit_code:
    raise RuntimeError(r.result[-4000:])
report = json.loads(r.result)
kind = "release-replay" if args.upgrade_runtime else "replay"
(directory / f"attempt-{attempt['number']}-{kind}.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
