#!/bin/bash
# Teardown the 3-namespace emulation topology
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudo "$DIR/setup_topology.sh" teardown
