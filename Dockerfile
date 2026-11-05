# terra-hatch container
# Base: the Terra builder image (anda, mock, terra-mock-configs included).
# Local use:
#   docker build -t terra-hatch .
#   docker run --rm -it -v "$PWD:/src" terra-hatch owner/repo --category apps
#   docker run --rm -it -v "$PWD:/src" --entrypoint anda  terra-hatch \
#       build -c terra-rawhide-x86_64 anda/<category>/<name>/pkg
FROM ghcr.io/terrapkg/builder:frawhide

COPY . /src
WORKDIR /src
RUN dnf install python3-pip -y
RUN python3 -m pip install . && rm -rf /src

ENTRYPOINT ["terra-hatch"]
