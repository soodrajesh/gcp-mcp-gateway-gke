"""Defence in depth: is each MCP server safe on its own, and is the network locked down?
Every block is paired with a positive control (the same probe succeeding where it should), so a broken probe cannot
pass as a 'blocked' result."""
import json, subprocess, sys, threading, time, uuid

RESULTS: list[bool] = []
NS = "mcp-platform"


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}")


def sh(cmd, timeout=120):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def probe(name, labels, url, ns=NS, timeout=4):
    """Run a throwaway curl pod; returns the HTTP status it saw, or 'BLOCKED' when the connection timed out."""
    lab = f"--labels={labels}" if labels else ""
    r = sh(f"kubectl -n {ns} run nettest-{name}-{uuid.uuid4().hex[:5]} --rm -i --restart=Never --quiet {lab} "
           f"--image=curlimages/curl:8.11.1 --command -- sh -c \"curl -m {timeout} -s -o /dev/null -w '%{{http_code}}' {url}; true\"", timeout=240)
    out = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
    return "BLOCKED" if out in ("000", "") else out


def main() -> int:
    print("\nF. Each server refuses anyone but the gateway (checked directly, bypassing the gateway)")
    ports = {"tickets": 18091, "finops": 18092, "k8s": 18093}
    pfs = [subprocess.Popen(f"kubectl -n {NS} port-forward svc/mcp-{n} {p}:8000", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for n, p in ports.items()]
    time.sleep(6)
    try:
        def post(port, auth=None):
            h = f"-H 'Authorization: Bearer {auth}'" if auth else ""
            r = sh(f"curl -s -o /dev/null -w '%{{http_code}}' -m 8 {h} -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' "
                   f"-d '{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}}' http://localhost:{port}/mcp")
            return r.stdout.strip()
        gtok = sh("gcloud auth print-identity-token").stdout.strip()
        for n, p in ports.items():
            check(f"{n}: /healthz answers without a token (probe path)", sh(f"curl -s -o /dev/null -w '%{{http_code}}' -m 8 http://localhost:{p}/healthz").stdout.strip() == "200")
            check(f"{n}: no token -> 401", post(p) == "401", post(p))
            check(f"{n}: garbage token -> 401", post(p, "abcdefg") == "401")
        check("tickets: a GENUINE Google-signed token for a different audience -> 401",
              gtok.count(".") == 2 and post(ports["tickets"], gtok) == "401", f"status={post(ports['tickets'], gtok) if gtok else 'no token'}")
    finally:
        for p in pfs:
            p.terminate()
        sh("pkill -f 'port-forward svc/mcp-' || true")

    print("\nG. Network lockdown (NetworkPolicy): only the gateway reaches the servers")
    tests = {
        "unlabelled->server": ("a", "", f"http://mcp-tickets.{NS}.svc.cluster.local:8000/healthz", NS),
        "otherns->server": ("b", "", f"http://mcp-tickets.{NS}.svc.cluster.local:8000/healthz", "default"),
        "client->gateway": ("c", "mcp-client=true", f"http://mcp-gateway.{NS}.svc.cluster.local/.well-known/oauth-protected-resource/mcp", NS),
        "client->server": ("d", "mcp-client=true", f"http://mcp-finops.{NS}.svc.cluster.local:8000/healthz", NS),
        "unlabelled->gateway": ("e", "", f"http://mcp-gateway.{NS}.svc.cluster.local/.well-known/oauth-protected-resource/mcp", NS),
        "unlabelled->keycloak": ("f", "", f"http://keycloak.{NS}.svc.cluster.local:8080/realms/mcp", NS),
    }
    got: dict[str, str] = {}

    def run(k, v):
        got[k] = probe(v[0], v[1], v[2], v[3])

    threads = [threading.Thread(target=run, args=(k, v)) for k, v in tests.items()]
    [t.start() for t in threads]; [t.join() for t in threads]
    check("a pod with no allow-label cannot reach a server", got["unlabelled->server"] == "BLOCKED", got["unlabelled->server"])
    check("a pod in another namespace cannot reach a server", got["otherns->server"] == "BLOCKED", got["otherns->server"])
    check("positive control: a pod labelled mcp-client CAN reach the gateway", got["client->gateway"] == "200", got["client->gateway"])
    check("... but even a labelled client cannot reach a server directly", got["client->server"] == "BLOCKED", got["client->server"])
    check("an unlabelled pod cannot reach the gateway", got["unlabelled->gateway"] == "BLOCKED", got["unlabelled->gateway"])
    check("an unlabelled pod cannot reach Keycloak", got["unlabelled->keycloak"] == "BLOCKED", got["unlabelled->keycloak"])

    print("\nH. Servers have limited egress (DNS, metadata, HTTPS only)")
    pod = sh(f"kubectl -n {NS} get pod -l app=mcp-tickets -o jsonpath='{{.items[0].metadata.name}}'").stdout.strip()
    code = "import socket,sys;s=socket.socket();s.settimeout(4);print('OPEN' if s.connect_ex((sys.argv[1],int(sys.argv[2])))==0 else 'BLOCKED')"

    def egress(host, port):
        return sh(f"kubectl -n {NS} exec {pod} -- python -c \"{code}\" {host} {port}", timeout=60).stdout.strip().splitlines()[-1:]

    check("positive control: a server can open HTTPS (443) to the internet", egress("1.1.1.1", 443) == ["OPEN"])
    check("a server cannot open plain HTTP (80) to the internet", egress("1.1.1.1", 80) == ["BLOCKED"])
    check("a server cannot open SMTP (25) to the internet", egress("1.1.1.1", 25) == ["BLOCKED"])

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


sys.exit(main())
