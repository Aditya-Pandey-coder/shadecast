# Only the ingest function needs this. ecCodes (the GRIB2 reader behind cfgrib) is a
# native library, and building it into a Lambda layer by hand is the classic way to
# lose four hours of a hackathon. A container image makes it a non-event.
FROM public.ecr.aws/lambda/python:3.12

RUN dnf install -y gcc gcc-c++ make cmake && dnf clean all

COPY requirements-ingest.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-ingest.txt

COPY shadecast/ ${LAMBDA_TASK_ROOT}/shadecast/
COPY aws/lambda_ingest.py ${LAMBDA_TASK_ROOT}/aws/
RUN touch ${LAMBDA_TASK_ROOT}/aws/__init__.py

CMD ["aws.lambda_ingest.handler"]
