# 中兴机顶盒 (ZTE STB) UDP 打洞/心跳报文结构

该报文总长度为 **84 字节**，用于在 RTSP 建立连接后，向服务端发送 UDP 报文进行打洞（NAT 穿越）和保活。

## 报文结构

| 偏移 (Byte) | 长度 (Byte) | 字段含义 | 示例值 (Hex) | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x00` | 8 | 设备标识 | `5a 58 56 31 30 53 54 42` | ASCII: `ZXV10STB` |
| `0x08` | 4 | 状态字段 | `7f ff ff ff` | 固定值 |
| `0x0c` | 4 | IP 地址 | `c0 a8 89 16` | 客户端本地 IP (如 `192.168.137.22`) |
| `0x10` | 4 | 魔数 (MAGIC) | `52 7a e4 64` | **核心校验字段**，不匹配则不推流 |
| `0x14` | 64 | 填充数据 | `00 00 ... 00` | 全 `0x00` 填充 |

## Python 构建示例

```python
import socket

def get_heartbeat_payload(ip_str):
    # 1. 设备标识 (8 字节) + 状态字段 (4 字节)
    header = b'ZXV10STB\x7f\xff\xff\xff'
    
    # 2. IP 地址转 16 进制字节 (4 字节)
    ip_bytes = bytes(int(x) for x in ip_str.split('.'))
    
    # 3. 魔数 MAGIC (4 字节)
    MAGIC = b'\x52\x7a\xe4\x64'
    
    # 4. 填充 0 (64 字节)
    padding = b'\x00' * 64
    
    return header + ip_bytes + MAGIC + padding
```
