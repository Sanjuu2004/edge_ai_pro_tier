import time


class FPSCounter:

    def __init__(self):

        self.last = time.time()

        self.frames = 0

        self.fps = 0

    def update(self):

        self.frames += 1

        now = time.time()

        elapsed = now - self.last

        if elapsed >= 1:

            self.fps = self.frames / elapsed

            self.frames = 0

            self.last = now

    def get(self):

        return round(self.fps, 2)


class Metrics:

    def __init__(self, camera_count):

        self.counter = {

            i: FPSCounter()

            for i in range(camera_count)

        }

    def update(self, cam):

        self.counter[cam].update()

    def fps(self, cam):

        return self.counter[cam].get()
