# CubeProxy TLS Configuration

CubeProxy exposes **both HTTPS (host port 443) and HTTP (host port 80)** simultaneously out of the box.

> **Note:** TLS configuration only affects how clients reach CubeProxy. `E2B_API_URL` always points to the **Cube API Server** (default port `3000`) — a separate component from CubeProxy.

## Overview

**Using the E2B SDK?**
The E2B SDK accesses sandboxes over HTTPS by default. Cube ships with a built-in DNS service and a pre-installed `cube.app` certificate so you can get started without any manual certificate setup.

**Not using the E2B SDK?**
You can interact with sandboxes over plain HTTP directly. Make sure the `Host` header in every request follows the format:

```
Host: <sandbox-service-port>-<sandboxId>-<domain>
```

where `<sandbox-service-port>` is the port your sandbox application listens on (e.g. `49999`), and `<domain>` defaults to `cube.app`, or your custom domain if you have configured one (see Option B below). For example:

```
Host: 49999-abc123def456-cube.app
```

---

## Option A — mkcert (Custom Domain Quick Verification)

If you want to use a custom hostname with HTTPS during development, `mkcert` generates a locally-trusted certificate in seconds.

```bash
mkcert -install
mkcert <your-host-ip-or-domain>
```

Set `SSL_CERT_FILE` so the E2B SDK trusts the generated CA:

```bash
export SSL_CERT_FILE=$(mkcert -CAROOT)/rootCA.pem
```

> mkcert certificates are only trusted on machines where `mkcert -install` has been run. Not suitable for production or shared deployments.

## Option B — Your Own Certificate / Domain (Production)

Edit CubeProxy's `nginx.conf` to use your certificate and private key:

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate     /path/to/your/cert.pem;
    ssl_certificate_key /path/to/your/key.pem;
}
```

### Update the sandbox domain

When using a custom domain, the Cube API Server must be told what domain to embed in sandbox response objects. Otherwise the `domain` field in API responses will still return the default value `cube.app` and clients will fail to connect to the sandbox.

Pass `--sandbox-domain` at startup, or set the equivalent environment variable:

```bash
# CLI flag
./cube-api --sandbox-domain your.domain.com

# Or via environment variable
export CUBE_API_SANDBOX_DOMAIN=your.domain.com
./cube-api
```

## Option C — HTTPS-Only (Disable HTTP)

By default, CubeProxy listens on both HTTP and HTTPS. If you want to disable the HTTP port entirely and serve only HTTPS, remove the HTTP server block from CubeProxy's `nginx.conf` and drop the corresponding port mapping in `docker-compose.yaml`.

> The E2B SDK only uses HTTPS, so disabling HTTP has no impact on SDK-based clients.
