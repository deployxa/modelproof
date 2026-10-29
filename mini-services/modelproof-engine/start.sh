#!/bin/bash
# ModelProof engine service (loopback-only, spec §10.4)
cd /home/z/my-project/modelproof
exec /home/z/my-project/modelproof/.venv/bin/modelproof serve --port 8899
