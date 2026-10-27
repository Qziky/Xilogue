"""Exclusive ownership prevents recovery from racing another running writer."""

import msvcrt


class DataLock:
    def __init__(self, path):
        self.file = path.open("a+b")
        try:
            if path.stat().st_size == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("data_directory_in_use") from exc

    def close(self):
        self.file.close()  # Kernel releases the byte lock, including after process death.
