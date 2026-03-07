import queue
import time

class Priority:
    SOS = 0
    CRITICAL_TELEMETRY = 1
    TELEMETRY = 2
    BULK = 3

class Message:
    def __init__(self, priority, node_id, direction, payload_bytes, risk_score=0.0, tag=""):
        self.priority = priority
        self.node_id = node_id
        self.direction = direction
        self.payload_bytes = payload_bytes
        self.risk_score = risk_score
        self.tag = tag
        self.ts = time.time()

    def __lt__(self, other):
        return self.priority < other.priority

class SatelliteScheduler:
    def __init__(self):
        self.status = 'GOOD'
        self.uplink_queue = queue.PriorityQueue()
        self.downlink_queue = queue.PriorityQueue()
        self.ul_consumed_kb = 0.0
        self.dl_consumed_kb = 0.0
        self.transmit_log = []

    def set_status(self, status):
        self.status = status

    def submit(self, msg: Message):
        if msg.direction == "uplink":
            self.uplink_queue.put(msg)
        else:
            self.downlink_queue.put(msg)

    def step(self):
        sent_this_step = 0
        temp_queue = []

        while not self.uplink_queue.empty():
            msg = self.uplink_queue.get()

            if self.status == 'DOWN':
                temp_queue.append(msg)
                continue

            if self.status == 'WEAK':
                if msg.priority == Priority.SOS and sent_this_step < 2:
                    self._send(msg)
                    sent_this_step += 1
                else:
                    temp_queue.append(msg)
            elif self.status == 'GOOD':
                if msg.priority == Priority.SOS:
                    self._send(msg)
                    sent_this_step += 1
                elif sent_this_step < 5:
                    self._send(msg)
                    sent_this_step += 1
                else:
                    temp_queue.append(msg)

        for msg in temp_queue:
            self.uplink_queue.put(msg)

        return sent_this_step

    def _send(self, msg):
        self.ul_consumed_kb += msg.payload_bytes / 1024.0
        self.transmit_log.append({
            "ts": time.time(),
            "tag": msg.tag,
            "priority": msg.priority,
            "bytes": msg.payload_bytes
        })

    def queue_length(self):
        return self.uplink_queue.qsize()
