"""Run the merged raw-TCP manager and Python TTY application controller.

Example (WSL): python3 tools/run_tty_lab.py --manager /path/to/mm_manager
  --state-dir /path/to/temporary/lab --number 1234 --tcp 0.0.0.0:16420
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def wait_port(port, process):
    deadline = time.monotonic() + 10
    while True:
        if process.poll() is not None:
            raise RuntimeError(f'Process exited before listening: {process.args}')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                return
        except ConnectionRefusedError:
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Listener did not start on port {port}')
            time.sleep(.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager', required=True, type=Path)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--number', default='1234')
    parser.add_argument('--tcp', default='0.0.0.0:16420')
    parser.add_argument('--controller-port', type=int, default=27274)
    parser.add_argument('--app', type=Path)
    parser.add_argument('--table-profile', type=Path,
                        help='Default table directory; generated terminal settings still use manager options')
    parser.add_argument('--ping', type=float, default=1,
                        help='Short keepalive for accelerated emulators; controller production default is 8')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manager = args.manager.resolve()
    app = (args.app or root / 'src/dialin/example.py').resolve()
    directory = args.state_dir.resolve()
    if not manager.is_file() or not app.is_file():
        parser.error('Manager and application must exist')
    directory.mkdir(exist_ok=True)
    config = directory / 'config'
    if not config.exists():
        shutil.copytree(root / 'config', config)
    (directory / 'tables').mkdir(exist_ok=True)
    stopped = False
    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    commands = [
        [sys.executable, '-u', str(root / 'src/dialin/server.py'), '--app', str(app),
         '--port', str(args.controller_port), '--ping', str(args.ping)],
        [str(manager), '-n', args.number, '-T', args.tcp, '-j', str(args.controller_port),
          '-d', str((args.table_profile or root / 'tables/default').resolve()), '-t', str(directory / 'tables'),
         '-vv', '-l', str(directory / 'session.dlog')],
    ]
    if shutil.which('stdbuf'):
        commands[1] = ['stdbuf', '-oL', '-eL', *commands[1]]
    processes, logs = [], []
    try:
        for index, command in enumerate(commands):
            log = (directory / ('controller.log' if index == 0 else 'manager.log')).open('wb')
            logs.append(log)
            process = subprocess.Popen(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            wait_port(args.controller_port if index == 0 else int(args.tcp.rsplit(':',1)[1]), process)
            time.sleep(.1)  # Readiness probes close their own empty sessions.
        state = dict(ready=True, parent_pid=os.getpid(),
                     manager_pid=processes[1].pid, controller_pid=processes[0].pid,
                     commands=commands, tcp=args.tcp, controller_port=args.controller_port,
                     state_directory=str(directory))
        (directory / 'ready.json').write_text(json.dumps(state, indent=2) + '\n')
        print(json.dumps(state), flush=True)
        while not stopped:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f'Child exited: {process.args}: {process.returncode}')
            time.sleep(.1)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()
