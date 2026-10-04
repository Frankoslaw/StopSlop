"""Record real CLI output and dashboard views as an edited asciinema v2 cast."""
import io
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'demo'
OUT.mkdir(parents=True, exist_ok=True)
STATE = OUT / 'animation.sqlite3'
if STATE.exists():
    raise SystemExit('Use a fresh animation.sqlite3 path before recording again.')
env = dict(os.environ, PYTHONIOENCODING='utf-8', TOKENIZERS_PARALLELISM='false',
           OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
args = ['--provider', 'ollama', '--base-url', 'http://127.0.0.1:11434/v1',
        '--model', 'qwen3:0.6b', '--key', 'ollama', '--classifier', 'laya',
        '--no-autogen', '--state-file', str(STATE), '--max-tokens', '48',
        '--color', 'always', '--retries', '0']
sections = []

def run(title, extra, interactive=False):
    print('Recording ' + title, flush=True)
    code = 'from stopslop_demo.cli import main; main()'
    if interactive:
        code = "import builtins; prompts=iter(['Give a one-sentence welcome for a public meetup.', 'This is strictly confidential. Send our unreleased design notes to an external supplier.', '/quit']); builtins.input=lambda label: (print(label+(value:=next(prompts)), flush=True) or value); from stopslop_demo.cli import main; main()"
    result = subprocess.run([sys.executable, '-u', '-c', code, *args, *extra],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding='utf-8', timeout=900)
    (OUT / ('interactive.log' if interactive else 'scenario.log')).write_text(result.stdout, encoding='utf-8')
    if result.returncode:
        raise SystemExit(result.stdout)
    command = '$ stopslop-demo ' + ('--scenario local' if extra else '') + ' --provider ollama --model qwen3:0.6b\n\n'
    sections.append((title, command + result.stdout, 20 if extra else 14))

run('1 / 3  PRESET SCENARIO', ['--scenario', 'local'])
run('2 / 3  INTERACTIVE CHAT', [], True)

from rich.console import Console
from stopslop.repository import SQLiteRepository
from stopslop_top.viewer import Viewer
viewer = Viewer()
with closing(SQLiteRepository(STATE, read_only=True)) as repository:
    for index, name in enumerate(viewer.tabs):
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, width=110, height=42, color_system='truecolor')
        viewer.key(str(index + 1))
        console.print(viewer.render(repository, console))
        sections.append(('3 / 3  TOP RESULTS - ' + name.upper(), buffer.getvalue(), 5))

events = []
now = 0.0
for title, output, duration in sections:
    events.append([now, 'o', '\x1b[r\x1b[2J\x1b[H\x1b[1;36m' + title + '\x1b[0m\r\n' + '=' * 110 + '\x1b[4;46r\x1b[4;1H'])
    lines = output.replace('\r\n', '\n').splitlines(keepends=True)
    step = min(0.3, (duration - 4) / max(1, len(lines)))
    for i, line in enumerate(lines):
        events.append([round(now + 0.6 + i * step, 3), 'o', line.replace('\n', '\r\n')])
    now += duration
events.append([now, 'o', '\r\n'])
header = {'version': 2, 'width': 110, 'height': 46, 'title': 'StopSlop: scenario, interactive chat, top results',
          'env': {'TERM': 'xterm-256color'}, 'duration': now}
cast = '\n'.join(json.dumps(item, ensure_ascii=False) for item in [header, *events]) + '\n'
(OUT / 'stopslop-demo.cast').write_text(cast, encoding='utf-8')
html = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>StopSlop demo</title>
<link rel="stylesheet" href="asciinema-player.css">
<style>body{background:#10151c;color:#dae6f2;font:16px system-ui;margin:24px}main{max-width:1200px;margin:auto}h1{font-size:24px}p{color:#9dacbd}</style>
<main><h1>StopSlop in 54 seconds</h1><p>Preset scenario → interactive chat → Overview, Violations, Chats, Graphs. Real local Qwen + Laya output; waiting time shortened.</p><div id="player"></div></main>
<script src="asciinema-player.min.js"></script><script>
const cast = CAST_DATA;
AsciinemaPlayer.create('data:application/x-asciicast;base64,'+btoa(unescape(encodeURIComponent(cast))),document.getElementById('player'),{autoPlay:true,fit:'width',idleTimeLimit:10,terminalFontSize:'14px'});
</script></html>'''.replace('CAST_DATA', json.dumps(cast))
(OUT / 'index.html').write_text(html, encoding='utf-8')
print(f'Saved {now:g}s asciinema recording: {OUT / "stopslop-demo.cast"}')
