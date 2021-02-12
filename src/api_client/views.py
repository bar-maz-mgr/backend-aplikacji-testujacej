from django.shortcuts import render

# Create your views here.
from drf_yasg2.openapi import Parameter
from drf_yasg2.utils import swagger_auto_schema, is_list_view
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework import status, serializers, viewsets
from rest_framework.views import APIView
from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse, HttpResponse
from itertools import groupby
import time
from django.db.models import Count
import json
import csv
from rest_framework.renderers import JSONRenderer
from datetime import datetime
from .serializers import *
from .models import *
from .tasks import run_test

import traceback


class TestView(APIView):
    testSerializer = TestSerializer
    testDetailsSerializer = TestDetailsSerializer

    @swagger_auto_schema(responses={200: testSerializer()},
                         manual_parameters=[Parameter(name="FRONT", in_='header', type='str')])
    def get(self, request, pk=None, format=None):
        if pk:
            serializer = self.get_single(request, pk, format).data
        else:
            serializer = self.get_many(request, format)
        return Response(serializer, status=status.HTTP_200_OK)

    def get_single(self, request, pk, format=None):
        tests = Test.objects.get(id=pk)
        ids = list(TestEndpoint.objects.values().filter(test_id=pk).values_list('endpoint_id', flat=True))
        endpoints = list(Endpoint.objects.values().filter(id__in=ids))
        for endpoint in endpoints:
            endpoint['order'] = TestEndpoint.objects.filter(test_id=pk).filter(endpoint_id=endpoint['id']).values(
                'order').values_list('order', flat=True)
        return self.testDetailsSerializer(tests, context={'endpoints': endpoints})

    def get_many(self, request, format=None):
        result = []
        tests = list(Test.objects.values())
        for test in tests:
            endpoints_count = TestEndpoint.objects.filter(test_id=test['id']).count()
            result.append(self.testSerializer(test, context={'endpoints_count': endpoints_count}).data)
        return result


class ResultView(APIView):
    testResultsSerializer = TestResultsSerializer
    testCallDetailsSerializer = TestCallDetailsSerializer
    
    @swagger_auto_schema(responses={200: testResultsSerializer()},
                         manual_parameters=[Parameter(name="FRONT", in_='header', type='str')])
    def get(self, request, pk=None, format=None):
        if pk:
            serializer = self.get_single(request, pk, format).data
        else:
            serializer = self.get_many(request, format)
        return Response(serializer, status=status.HTTP_200_OK)

    def get_single(self, request, pk, format=None):
        tests = Test.objects.get(id=pk)
        testCall_ids = list(TestCall.objects.values().filter(test_id=pk).values_list('id', flat=True))
        testCalls = list(TestCall.objects
                         .values('id', 'start_date', 'end_date', 'num_users', 'max_calls', 'is_finished', 'is_finished')
                         .filter(id__in=testCall_ids).order_by('-start_date'))

        for testCall in testCalls:
            results = list(Result.objects.values('results')
                           .filter(test_call_id=testCall['id'])
                           .values_list('results', flat=True))
            json_results = []
            for result_str in results:
                json_results.append(json.loads(result_str))
            testCall['results'] = json_results
        return self.testResultsSerializer(tests, context={'testCalls': testCalls})

    def get_many(self, request, format=None):
        test_calls = TestCall.objects.all().order_by('-start_date')
        serializer = self.testCallDetailsSerializer(test_calls, many=True)
        for test_call in serializer.data:
            test_call['name'] = test_call['test']['name']
            test_call.pop('test')
        return serializer.data


class TestCallView(APIView):
    serializer_class = TestCallSerializer

    @swagger_auto_schema(responses={200: serializer_class()},
                         manual_parameters=[Parameter(name="FRONT", in_='header', type='str')])
    def get(self, request, pk=None, format=None):
        if pk:
            try:
                test_call = TestCall.objects.get(id=pk)
                serializer = self.serializer_class(test_call)
                return Response(serializer.data, status=status.HTTP_200_OK)
            except Result.DoesNotExist:
                return Response({'error': 'Result not found'}, status=status.HTTP_404_NOT_FOUND)
        else:
            test_calls = TestCall.objects.all()
            serializer = self.serializer_class(test_calls, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        test_call = TestCall.objects.last()
        if test_call is None or test_call.is_finished:
            serializer = TestCallInputSerializer(data=request.data, fields=('test', 'num_users', 'max_calls'))
            if serializer.is_valid():
                test_id = serializer.validated_data['test']
                try:
                    test_call = TestCall.objects.create(
                        test=test_id,
                        num_users=serializer.validated_data["num_users"],
                        max_calls=serializer.validated_data["max_calls"]
                    )
                    save_serializer = TestCallSerializer(test_call)
                    run_test.delay(save_serializer.data)
                    return Response(save_serializer.data, status=status.HTTP_201_CREATED)
                except Exception as e:
                    print(traceback.format_exc())
                    return JsonResponse({'error': str(e)}, safe=False, status=status.HTTP_400_BAD_REQUEST)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'error': 'Another test is already running'}, status=status.HTTP_400_BAD_REQUEST)

class ActiveTestCallView(APIView):
    serializer_class = TestCallSerializer

    @swagger_auto_schema(responses={200: serializer_class()},
                         manual_parameters=[Parameter(name="FRONT", in_='header', type='str')])
    def get(self, request, pk=None, format=None):
        test_call = TestCall.objects.last()
        if test_call is not None and test_call.is_finished:
            return Response({'error': 'There are no active test'}, status=status.HTTP_404_NOT_FOUND)
        else:
            serializer = self.serializer_class(test_call)
            return Response(serializer.data, status=status.HTTP_200_OK)


class TestCallDetailsView(APIView):
    serializer_class = TestCallDetailsSerializer

    @swagger_auto_schema(responses={200: serializer_class()},
                         manual_parameters=[Parameter(name="FRONT", in_='header', type='str')])
    def get(self, request, pk=None, format=None):
        if pk:
            try:
                test_call = TestCall.objects.get(id=pk)
                serializer = self.serializer_class(test_call)
                data = serializer.data
                results = Result.objects.filter(test_call=test_call)
                json_results = []
                for result in results:
                    res = json.loads(result.results)
                    if 'container_id' in res:
                        json_results.append(res)
                json_results.sort(key=lambda x: x['container_id'])
                groups = groupby(json_results, lambda x: x['container_id'])
                
                results_grouped = {}
                for result, group in groups:
                    tmp = []
                    for content in group:
                        tmp.append(content)
                    results_grouped[result] = tmp
                    
                data["results"] = results_grouped
                
                return Response(data, status=status.HTTP_200_OK)
            except TestCall.DoesNotExist:
                return Response({'error': 'Test Call not found'}, status=status.HTTP_404_NOT_FOUND)
        else:
            result = []
            tests = list(Test.objects.values())
            for test in tests:
                testCall_ids = list(TestCall.objects.values().filter(test_id=test['id']).values_list('id', flat=True))
                testCalls = list(TestCall.objects
                                 .values('id', 'test', 'start_date', 'end_date', 'num_users', 'max_calls', 'is_finished')
                                 .filter(id__in=testCall_ids).order_by('-start_date'))
                result.append(testCalls)
            return Response({'error': 'No pk specified'}, status=status.HTTP_404_NOT_FOUND)


class TestCallJsonView(APIView):
    serializer_class = TestCallDetailsSerializer

    @swagger_auto_schema(responses={200: serializer_class()},
                         manual_parameters=[Parameter(name="FRONT", in_='header', type='str')])
    def get(self, request, pk=None, format=None):
        if pk:
            try:
                test_call = TestCall.objects.get(id=pk)
                results = Result.objects.filter(test_call=test_call)
                json_results = []
                for result in results:
                    json_results.append(json.loads(result.results))
                json_name = str(test_call.id) + test_call.start_date.strftime("D%d_%m_%YT%H_%M_%S") + ".json"
                response = Response(json_results, content_type='application/json')
                response['Content-Disposition'] = 'attachment; filename=%s' % json_name
                return response
            except TestCall.DoesNotExist:
                return Response({'error': 'Test Call not found'}, status=status.HTTP_404_NOT_FOUND)
        else:
            return Response({'error': 'No pk specified'}, status=status.HTTP_404_NOT_FOUND)



class TestCallCSVView(APIView):
    serializer_class = TestCallDetailsSerializer

    @swagger_auto_schema(responses={200: serializer_class()},
                         manual_parameters=[Parameter(name="FRONT", in_='header', type='str')])
    def get(self, request, pk=None, format=None):
        if pk:
            try:
                test_call = TestCall.objects.get(id=pk)
                results = Result.objects.filter(test_call=test_call)
                csv_name = str(test_call.id) + test_call.start_date.strftime("D%d_%m_%YT%H_%M_%S") + ".csv"
                response = HttpResponse(content_type='text/csv')
                response['Content-Disposition'] = 'attachment; filename=%s' % csv_name
                writer = csv.writer(response)
                writer.writerow(["timestamp", "num_sql_queries", "time_spent_on_sql_queries", "time_taken", "cpu_usage_current",
                                 "cpu_time_spent_user", "cpu_time_spent_system", "cpu_time_spent_idle", "memory_usage", "container_id"])
                for result in results:
                    r = json.loads(result.results)
                    r.get("cpu_usage_current")
                    cpu_current = r.get("cpu_usage_current")
                    if cpu_current:
                        try:    
                            timestamp = datetime.strptime(cpu_current.get("timestamp"), "%Y-%m-%d %H:%M:%S.%f").timestamp()
                        except:
                            timestamp = datetime.strptime(cpu_current.get("timestamp"), "%Y-%m-%d %H:%M:%S").timestamp()
                    else:
                        timestamp = None
                    writer.writerow([timestamp, r.get("num_sql_queries"), r.get("time_spent_on_sql_queries"),
                                    r.get("time_taken"), cpu_current.get("usage") if cpu_current else None, r.get("cpu_time_spent_user"),
                                    r.get("cpu_time_spent_system"), r.get("cpu_time_spent_idle"), r.get("memory_usage"),
                                    r.get("container_id")])
                return response
            except TestCall.DoesNotExist:
                return Response({'error': 'Test Call not found'}, status=status.HTTP_404_NOT_FOUND)
        else:
            return Response({'error': 'No pk specified'}, status=status.HTTP_404_NOT_FOUND)


class TestCallByDateView(APIView):
    serializer_class = TestCallSerializer

    @swagger_auto_schema(responses={200: serializer_class()},
                         manual_parameters=[Parameter(name="FRONT", in_='header', type='str')])
    def get(self, request, test_date=None, format=None):
        if test_date:
            try:
                date = datetime.strptime(test_date, '%d-%m-%Y')
            except ValueError as e:
                return Response({'error': 'Wrong date format! Use \'dd-mm-yyyy\'.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            date = datetime.now()
        data = self.get_many(request, date, format)
        return Response(data, status=status.HTTP_200_OK)

    def get_many(self, request, date, format=None):
        test_calls = TestCall.objects.filter(start_date__date=date.date())
        serializer = self.serializer_class(test_calls, many=True)
        return serializer.data
