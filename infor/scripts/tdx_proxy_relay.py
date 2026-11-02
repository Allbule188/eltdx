#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""让 eltdx 的 7709 通道走代理 —— 本地 relay 方案。

背景
--------------------------------------------------------------------------------
实测 eltdx **不支持代理**：
  * `TdxClient` / `from_hosts` 参数里没有任何 proxy/socks 项（已查源码）
  * 源码里的 "PinnedTransportProxy" 是「固定到某个连接槽」，不是网络代理
  * Rust 引擎直接建 TcpStream，中间没有代理层
  * 只有 F10（7615）那条走 urllib，会读 HTTPS_PROXY 环境变量

但实测 eltdx **接受任意 host:port 字符串（含 127.0.0.1）**，
所以可以用本地中转：eltdx 连本地，本地 relay 通过 SOCKS5/HTTP 代理转发到真主站。
**不用改 eltdx 一行代码。**

架构
--------------------------------------------------------------------------------
    10~40 个 TdxClient  ──>  127.0.0.1:17709  ──[SOCKS5/HTTP代理]──>  主站:7709

用法
--------------------------------------------------------------------------------
    # SOCKS5 代理（带认证）
    python tdx_proxy_relay.py --proxy socks5://user:pass@1.2.3.4:1080

    # HTTP 代理
    python tdx_proxy_relay.py --proxy http://1.2.3.4:8080

    # 不指定 --proxy 则直连（只做端口映射，可用于把远端主站映射到本地）
    python tdx_proxy_relay.py --target 43.139.95.83:7709

然后在 Python 里：

    from eltdx import TdxClient
    c = TdxClient(host="127.0.0.1:17709", probe_hosts=False, timeout=20)

依赖
--------------------------------------------------------------------------------
    SOCKS5 需要 PySocks：  pip install pysocks
    HTTP 代理用标准库，无需额外依赖。

注意
--------------------------------------------------------------------------------
  * 每条 eltdx 连接 = 一条本地连接 = 一条到代理的出站连接。
    40 连接就是 40 条出站，代理必须扛得住。
  * 走代理必然增加延迟（多一跳）。我们的实测显示主站之间本来就差 5 倍，
    代理选得好可能反而比某些慢主站快。
  * relay 本身是纯字节转发，不解析协议，所以对 eltdx 完全透明。
"""

import argparse
import socket
import sys
import threading
import urllib.parse

BUF = 65536


def _connect_direct(host, port, timeout):
    s = socket.create_connection((host, port), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s


def _connect_socks5(proxy_url, host, port, timeout):
    import socks  # PySocks
    u = urllib.parse.urlparse(proxy_url)
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, u.hostname, u.port or 1080,
                username=u.username, password=u.password)
    s.settimeout(timeout)
    s.connect((host, port))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s


def _connect_http(proxy_url, host, port, timeout):
    """HTTP CONNECT 隧道。"""
    u = urllib.parse.urlparse(proxy_url)
    s = socket.create_connection((u.hostname, u.port or 8080), timeout=timeout)
    auth = ""
    if u.username:
        import base64
        raw = f"{u.username}:{u.password or ''}".encode()
        auth = "Proxy-Authorization: Basic " + base64.b64encode(raw).decode() + "\r\n"
    req = (f"CONNECT {host}:{port} HTTP/1.1\r\n"
           f"Host: {host}:{port}\r\n{auth}\r\n")
    s.sendall(req.encode())
    # 读响应头
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise ConnectionError("代理在 CONNECT 阶段断开")
        buf += chunk
    if not buf.split(b"\r\n", 1)[0].startswith(b"HTTP/1.1 200") and \
       not buf.split(b"\r\n", 1)[0].startswith(b"HTTP/1.0 200"):
        raise ConnectionError(f"代理拒绝 CONNECT: {buf.split(chr(13).encode())[0]!r}")
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s


def _pipe(src, dst, name, stats):
    """单向转发。用阻塞 socket + sendall，避免非阻塞半写丢失数据。

    （先前版本用非阻塞 + selectors，sendall 在缓冲区满时抛 BlockingIOError
      会被误判成错误而断连——已改回阻塞模型。）
    """
    try:
        while True:
            data = src.recv(BUF)
            if not data:
                break
            dst.sendall(data)
    except OSError as e:
        stats["errors"] = stats.get("errors", 0) + 1
        if stats.get("verbose"):
            print(f"  [relay] {name}: {e}", file=sys.stderr)
    finally:
        # 一个方向结束就整体收摊：先关写端，让对端感知 EOF
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


def _handle(client_sock, target, proxy_url, timeout, stats):
    up = None
    try:
        if proxy_url.startswith("socks5"):
            up = _connect_socks5(proxy_url, *target, timeout)
        elif proxy_url.startswith("http"):
            up = _connect_http(proxy_url, *target, timeout)
        else:
            up = _connect_direct(*target, timeout)
        up.settimeout(None)                 # 转发阶段不做超时，交给 eltdx 控制
        client_sock.settimeout(None)

        t = threading.Thread(target=_pipe,
                             args=(up, client_sock, "上游->客户端", stats),
                             daemon=True)
        t.start()
        _pipe(client_sock, up, "客户端->上游", stats)
        t.join(timeout=5)
    except Exception as e:
        stats["errors"] = stats.get("errors", 0) + 1
        if stats.get("verbose"):
            print(f"  [relay] 建连失败: {e}", file=sys.stderr)
    finally:
        for s in (client_sock, up):
            try:
                if s:
                    s.close()
            except Exception:
                pass
        stats["done"] = stats.get("done", 0) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:17709",
                    help="本地监听地址（eltdx 连这个）")
    ap.add_argument("--target", default="",
                    help="真实主站 host:port；不填则用 eltdx 默认排名的第一台")
    ap.add_argument("--proxy", default="",
                    help="socks5://[user:pass@]host:port 或 http://host:port；空=直连")
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    lh, _, lp = a.listen.partition(":")
    lp = int(lp or 17709)

    if a.target:
        th, _, tp = a.target.partition(":")
        target = (th, int(tp or 7709))
    else:
        from eltdx import TdxClient
        with TdxClient(timeout=8) as c:
            h = c.transport.hosts[0]
        th, _, tp = h.partition(":")
        target = (th, int(tp))
        print(f"未指定 --target，自动选用 {h}")

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((lh, lp))
    srv.listen(128)

    via = a.proxy or "直连"
    print(f"\n本地监听 {lh}:{lp}  ->  {target[0]}:{target[1]}   经 {via}")
    print(f"eltdx 用法: TdxClient(host=\"{lh}:{lp}\", probe_hosts=False)\n")

    stats = {"verbose": a.verbose, "total": 0}
    try:
        while True:
            cs, addr = srv.accept()
            stats["total"] += 1
            if a.verbose:
                print(f"[relay] #{stats['total']} 来自 {addr[0]}", flush=True)
            threading.Thread(target=_handle,
                             args=(cs, target, a.proxy, a.timeout, stats),
                             daemon=True).start()
    except KeyboardInterrupt:
        print(f"\n退出。共转发 {stats['total']} 条连接，"
              f"结束 {stats.get('done',0)}，错误 {stats.get('errors',0)}")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
