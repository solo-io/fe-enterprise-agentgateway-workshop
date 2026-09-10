# Mini Lab

## Deploy the echo server

```bash
kubectl create namespace mini
```

```bash
kubectl apply -n mini -f echo.yaml
```

## Send a request

1. Indented fence inside a list:

   ```bash
   curl -i "$GATEWAY_IP:8080/echo"
   ```

Expected output:

```json
{"not": "collected"}
```

## Cleanup

```bash
kubectl delete namespace mini --ignore-not-found
```
