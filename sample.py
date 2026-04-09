import time

class SamplesManager():
    def __init__(self, slot_id, log_queue, **kwargs):
        pass

    def sample(self, slot_id, log_queue, speed="0", user="Unknown", **kwargs):
        """
        Modified to pipe output to the log_queue instead of direct printing.
        """
        def log(msg):
            log_queue.put((slot_id, msg))

        log(f"--- [EVENT START: sample.py] ---")
        log(f"Target Speed: {speed}")
        log(f"Authorized User: {user}")

        # Simulated work to demonstrate interleaving
        for i in range(10000000):
            log(f"speed1={speed} and index={i}")
            time.sleep(0.5)

        if kwargs:
            log(f"Extended Metadata: {kwargs}")

        log("--- [EVENT FINISHED] ---")
