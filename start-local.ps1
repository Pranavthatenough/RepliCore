# start-local.ps1  -  run everything on Windows WITHOUT Docker.
#
# Needs Python 3.10+ and Node.js 18+. First time only, install the packages:
#     pip install -r replica\requirements.txt
#     cd gateway; npm install; cd ..
#
# Then run (from the project folder):
#     powershell -ExecutionPolicy Bypass -File .\start-local.ps1
#
# It opens 4 windows: node1, node2, node3 and the gateway.
# Close a node's window to "kill" it for real. Open http://localhost:3000

$root = $PSScriptRoot

$nodes = @(
    @{ id = "node1"; port = 5001; peers = "http://localhost:5002,http://localhost:5003" },
    @{ id = "node2"; port = 5002; peers = "http://localhost:5001,http://localhost:5003" },
    @{ id = "node3"; port = 5003; peers = "http://localhost:5001,http://localhost:5002" }
)

foreach ($n in $nodes) {
    $cmd = "`$host.UI.RawUI.WindowTitle = '$($n.id)'; " +
           "cd '$root\replica'; " +
           "`$env:NODE_ID = '$($n.id)'; `$env:PEERS = '$($n.peers)'; " +
           "python -m uvicorn server:app --port $($n.port) --log-level warning"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd
}

Start-Sleep -Seconds 2
$gw = "`$host.UI.RawUI.WindowTitle = 'gateway'; cd '$root\gateway'; node index.js"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $gw

Write-Host "Started 3 replicas + gateway. Open http://localhost:3000"
