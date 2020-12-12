import json
import os
import random
if os.name == "nt":
    import multiprocessing
else:
    import billiard as multiprocessing

from .LoadTester import load_tests, abstract
from .LoadTester.proc_func import process_function
from .celery import app
from .models import TestCall, Result

import typing
import inspect
import requests

from datetime import datetime

import time

from .serializers import TestCallSerializer


def filter_classes(o):
    return inspect.isclass(o) and 'LoadTesterBase' in map(lambda x: x.__name__, inspect.getmro(o))


@app.task
def run_test(test_call_str: str):
    test_call_dict = TestCallSerializer(test_call_str).instance
    test_call = TestCall.objects.get(pk = test_call_dict['id'])
    classes = inspect.getmembers(load_tests, filter_classes)
    print(classes)
    print(test_call)
    needed_class = list(filter(lambda x: x[0] == test_call.test.class_name, classes))
    if len(needed_class) != 1:
        raise TypeError('Cannot find described class: %s' % test_call.test.class_name)

    needed_class = needed_class[0]
    counter = multiprocessing.Value('i', 0)
    lock = multiprocessing.Lock()
    processes = []
    for i in range(test_call.num_users):
        from django import db
        db.connections.close_all()
        proc = multiprocessing.Process(target=process_function, args=(needed_class, test_call.max_calls, counter, lock, test_call_dict))
        proc.start()
        processes.append(proc)

    for proc in processes:
        print("WAITING FOR: ", proc)
        proc.join(timeout=3600)

    for proc in processes:
        if proc.is_alive():
            proc.terminate()

    try:
        print("WAITING FISHED")
        result = requests.post("%s/rest-auth/login/" % os.getenv("BACKEND_URL"),
                                json={"username": os.getenv("BACKEND_USER"), "password": os.getenv("BACKEND_PASSWORD")})
        print("STARTING FILE WRITTING")
        with open('prices%s.txt' % test_call.start_date.strftime("%m-%d-%Y %H-%M-%S"), 'w') as f:
            f.write(json.dumps(requests.get("%s/price_history/" % os.getenv("BACKEND_URL"), headers={"OBCIAZNIK": "DUPA", "Authorization": "Bearer %s" % result.json()['token']}).json()))

        with open('transactions%s.txt' % test_call.start_date.strftime("%m-%d-%Y %H-%M-%S"), 'w') as f:
            f.write(json.dumps(requests.get("%s/transaction/" % os.getenv("BACKEND_URL"), headers={"OBCIAZNIK": "DUPA", "Authorization": "Bearer %s" % result.json()['token']}).json()))
        print("FILE FINISHED")
    except Exception:
        pass
    test_call.is_finished = True
    test_call.end_date = datetime.now()
    test_call.save()
    print("SAVE TEST CALL FINISHED")

