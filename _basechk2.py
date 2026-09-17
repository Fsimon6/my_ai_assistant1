import sys, multiprocessing

def child():
    import sys
    print("MP_CHILD=" + sys.executable, flush=True)

if __name__ == "__main__":
    print("MP_PARENT=" + sys.executable, flush=True)
    p = multiprocessing.get_context("spawn").Process(target=child)
    p.start()
    p.join()
