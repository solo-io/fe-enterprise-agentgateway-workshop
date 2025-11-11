# Configure Route to MCP Server

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Deploy an MCP server example
- Route to the MCP server using agentgateway
- Validate MCP server connectivity using MCP Inspector


### Configure MCP server
```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-website-fetcher
  namespace: gloo-system
spec:
  selector:
    matchLabels:
      app: mcp-website-fetcher
  template:
    metadata:
      labels:
        app: mcp-website-fetcher
    spec:
      containers:
      - name: mcp-website-fetcher
        image: ghcr.io/peterj/mcp-website-fetcher:main
        imagePullPolicy: Always
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-website-fetcher
  namespace: gloo-system
  labels:
    app: mcp-website-fetcher
spec:
  selector:
    app: mcp-website-fetcher
  ports:
  - port: 80
    targetPort: 8000
    appProtocol: kgateway.dev/mcp
EOF
```

### Create backend and HTTPRoute
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-backend
  namespace: gloo-system
spec:
  type: MCP
  mcp:
    targets:
    - name: mcp-target
      static:
        host: mcp-website-fetcher.gloo-system.svc.cluster.local
        port: 80
        protocol: SSE
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp
  namespace: gloo-system
spec:
  parentRefs:
  - name: agentgateway
  rules:
    - backendRefs:
      - name: mcp-backend
        group: gateway.kgateway.dev
        kind: Backend
EOF
```

### Get gateway IP
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo $GATEWAY_IP
``` 

### Run the MCP Inspector
```bash
npx modelcontextprotocol/inspector#0.16.2
```

In the MCP Inspector menu, connect to your agentgateway
- Transport Type: Select Streamable HTTP.
- URL: Enter the agentgateway address, port, and the /mcp path. If your agentgateway proxy is exposed with a LoadBalancer server, use http://<lb-address>:8080/mcp. In local test setups where you port-forwarded the agentgateway proxy on your local machine, use http://localhost:8080/mcp.
- Click Connect.

### Fetch a website
- From the menu bar, click the Tools tab. Then from the Tools pane, click List Tools and select the fetch tool.
- From the fetch pane, in the url field, enter a website URL, such as https://lipsum.com/, and click Run Tool.
- Verify that you get back the fetched URL content.


## View all metrics
All metrics
```bash
echo
echo "Objective: curl /metrics endpoint and show all metrics"
kubectl port-forward -n gloo-system deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
``` 

Filter for number of requests served through the gateway
```bash
echo
echo "Objective: curl /metrics endpoint and filter for number of requests served through the gateway"
kubectl port-forward -n gloo-system deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep agentgateway_requests_total && kill $!
``` 

Total input and output token usage through the gateway
```bash
echo
echo "Objective: curl /metrics endpoint and filter for input/output token usage through the gateway"
kubectl port-forward -n gloo-system deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep agentgateway_gen_ai_client_token_usage_sum && kill $!
``` 
You can tell the difference between the two metrics from the `gen_ai_token_type="input/output"` label

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/agentgateway -n gloo-system --tail 1
```

Example output
```
{"level":"info","time":"2025-11-11T18:11:19.747638Z","scope":"request","gateway":"gloo-system/agentgateway","listener":"http","route":"gloo-system/mcp","src.addr":"10.42.0.1:65003","http.method":"POST","http.host":"192.168.107.2","http.path":"/mcp","http.version":"HTTP/1.1","http.status":200,"trace.id":"ba8e0e8c4138978666eecdc5d494f00a","span.id":"b9a6ff1a457183f3","mcp.method":"tools/call","mcp.target":"mcp-target","mcp.resource":"tool","mcp.resource.name":"fetch","duration":"476ms","rq.headers.all":{"accept-encoding":"gzip, deflate","user-agent":"node","sec-fetch-mode":"cors","mcp-session-id":"0d1ce700-447b-49e3-a97a-212a2b091ca1","content-length":"140","content-type":"application/json","accept-language":"*","accept":"application/json, text/event-stream"}}
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see traces for agentgateway that include information such as `mcp.method`, `mcp.resource`, `mcp.resource.name`, `mcp.target`, and more

## Cleanup
```bash
kubectl delete deployment -n gloo-system mcp-website-fetcher
kubectl delete service -n gloo-system mcp-website-fetcher
kubectl delete backend -n gloo-system mcp-backend
kubectl delete httproute -n gloo-system mcp
```