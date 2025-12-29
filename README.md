# PBFT Consensus Simulator

## 1. Giới thiệu

**PBFT Consensus Simulator** là một ứng dụng mô phỏng thuật toán đồng thuận **PBFT (Practical Byzantine Fault Tolerance)** trong môi trường mạng ngang hàng (peer-to-peer).
Hệ thống được thiết kế theo kiến trúc **tách biệt giữa tầng consensus và tầng giao diện**, giống cách các hệ thống phân tán thực tế thường triển khai.

- Các **PBFT replica** chạy độc lập dưới dạng **process riêng**
- Các replica **giao tiếp trực tiếp với nhau qua gRPC**
- **Streamlit** chỉ đóng vai trò **UI quan sát và gửi request**, không tham gia vào protocol

---

## 2. Kiến trúc tổng thể

```

+--------------------+
|    Streamlit UI    |
|     (Observer)     |
+----------+---------+
		   |
		   | Status / SubmitRequest
		   v
+--------------------+
|   PBFT Replica 1   | <---- gRPC ----> PBFT Replica 2
|  (Primary/Replica) | <---- gRPC ----> PBFT Replica 3
+--------------------+ <---- gRPC ----> PBFT Replica 4

```

### Phân tách trách nhiệm

| Thành phần | Vai trò |
|----------|--------|
| PBFT Replica | Xử lý 3 pha: PRE-PREPARE → PREPARE → COMMIT, thực thi request khi đạt quorum |
| RPC Replica ↔ Replica | PrePrepare, Prepare, Commit |
| Streamlit | Hiển thị trạng thái cluster và gửi client request |
| RPC UI ↔ Replica | GetStatus, SubmitRequest (và các RPC tiện ích như Ping/KillNode) |

**Streamlit không phải là một PBFT replica**.

---

## 3. Công nghệ sử dụng

- **gRPC** cho giao tiếp RPC
- **Streamlit** cho giao diện mô phỏng
- **Multiprocessing / Subprocess** để chạy nhiều node trên **1 máy**

Ghi chú: Cluster manager hiện dùng `cmd.exe`/`taskkill` nên phù hợp nhất với **Windows**.

---

## 4. Cấu trúc thư mục

```

project/
│
├── core/
│   ├── node.py            # PBFT replica implementation
│   ├── state.py           # PBFT state & in-memory log
│   ├── cluster.py         # Cluster manager (spawn replicas)
│   ├── raft.py            # (cũ) logic Raft, hiện không dùng
│   └── ...         
|
├── rpc/
│   ├── pbft.proto
│   ├── pbft_pb2.py
│   ├── pbft_pb2_grpc.py
│   ├── server.py          # RPC server for each replica
│   └── client.py          # RPC client utilities
│
├── ui/
│   ├── sidebar.py         # Streamlit sidebar controls
│   ├── cluster_view.py    # HTML visualization
│   └── utils/          
│
├── streamlit_app.py       # Streamlit entry point
├── run_node.py            # Init a PBFT replica
├── send_request.py        # Send a client request via gRPC
├── requirements.txt
└── README.md

```

---

## 5. Cách cài đặt

### 5.1 Tạo môi trường ảo

```bash
python -m venv venv
source venv/bin/activate
```

### 5.2 Cài đặt thư viện

```bash
pip install -r requirements.txt
```

---

## 6. Chạy hệ thống

### 6.1 Khởi động các PBFT replica (manual)

Mỗi replica chạy như **một process riêng**:

```bash
python run_node.py --id 1 --port 5001
python run_node.py --id 2 --port 5002
python run_node.py --id 3 --port 5003
...

Nếu muốn các node nhìn thấy nhau, truyền `--peers` theo format `id@host:port` (comma-separated). Ví dụ cho 4 node:

```bash
python run_node.py --id 1 --port 5001 --peers "1@localhost:5001,2@localhost:5002,3@localhost:5003,4@localhost:5004"
python run_node.py --id 2 --port 5002 --peers "1@localhost:5001,2@localhost:5002,3@localhost:5003,4@localhost:5004"
python run_node.py --id 3 --port 5003 --peers "1@localhost:5001,2@localhost:5002,3@localhost:5003,4@localhost:5004"
python run_node.py --id 4 --port 5004 --peers "1@localhost:5001,2@localhost:5002,3@localhost:5003,4@localhost:5004"
```

PBFT classic yêu cầu số node `n = 3f + 1` (ví dụ: 4, 7, 10).
```

---

### 6.2 Chạy Streamlit UI (khuyến nghị)

```bash
streamlit run streamlit_app.py
```

Mở trình duyệt tại:

```
http://localhost:<PORT>
```

Trong UI:

- Chọn số node hợp lệ (4, 7, 10)
- Start/Stop cluster
- Gửi client request (UI sẽ gửi vào 1 node bất kỳ; nếu node đó không phải Primary thì nó sẽ forward sang Primary)

---

## 7. Những gì đang được mô phỏng (theo code hiện tại)

### 7.1 PBFT 3-phase

- Client gửi `SubmitRequest` vào 1 replica
- Nếu replica không phải **Primary**, request được forward sang Primary (tránh loop)
- Primary multicast `PrePrepare`
- Replica multicast `Prepare`
- Khi đạt `2f + 1` prepare → multicast `Commit`
- Khi đạt `2f + 1` commit và đã `Prepared` → **Execute** (demo: echo payload)

### 7.2 Quan sát trạng thái

- UI polling `GetStatus` để hiển thị `role`, `view`, `primary_id`, `f`

---

## 8. Streamlit có vai trò gì?

* ❌ Không tham gia consensus
* ❌ Không trung gian message giữa các replica
* ✅ Quan sát trạng thái cluster
* ✅ Gửi client request để demo luồng PBFT

> Nếu Streamlit dừng, cluster PBFT vẫn tiếp tục hoạt động bình thường.

---

## 9. (Tuỳ chọn) Gửi request bằng CLI

```bash
python send_request.py --addr localhost:5001 --payload "hello"
```

---

## 10. Regenerate gRPC stubs (khi sửa proto)

Nếu bạn chỉnh [rpc/pbft.proto](rpc/pbft.proto), chạy lại:

```bash
python -m grpc_tools.protoc -I. --python_out=rpc --grpc_python_out=rpc rpc/pbft.proto
```

---
