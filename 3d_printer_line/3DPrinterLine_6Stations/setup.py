from setuptools.command.build_ext import build_ext
from distutils.core import setup, Extension
import os, sys

XL_VIP_HOME  = os.getenv('XL_VIP_HOME')
UVMC_HOME    = os.getenv('UVMC_HOME')
UVMC_LIB_LIBS = UVMC_HOME + '/lib/rawc_tlm/client/' + os.getenv('UVMC_BUILD_PLATFORM')
XL_VIP_LIBS = XL_VIP_HOME + '/lib/lib_rawc_tlm/' + os.getenv('XL_BUILD_PLATFORM')
INNEXIS_VSI_LIBS = os.getenv('INNEXIS_VSI_LIBS')
INNEXIS_VSI_HOME = os.getenv('INNEXIS_VSI_HOME')
PYTHON_GATEWAYS = 'pythonGateways/'

SRCS = []
INCLUDES = []
LIBRARIES = []
LIBRARY_DIRS = []
LDFLAGS = []

LIBRARIES.append('uvmc_tlm_fabric')
LIBRARIES.append('xl_vip_open_kit')
LIBRARIES.append('xl_vip_tlm_xactors')
LIBRARIES.append('xl_vip_open_kit_stubs')
LIBRARIES.append('xl_vip_open_kit_extras')
LIBRARIES.append('VsiGateways')

LIBRARY_DIRS.append(UVMC_LIB_LIBS)
LIBRARY_DIRS.append(XL_VIP_LIBS)
LIBRARY_DIRS.append(INNEXIS_VSI_LIBS + '/' +os.getenv('UVMC_BUILD_PLATFORM') +'/remote')

LDFLAGS.append('-Wl,-rpath,' + UVMC_LIB_LIBS)
LDFLAGS.append('-Wl,-rpath,' + XL_VIP_LIBS)
LDFLAGS.append('-Wl,-rpath,' + INNEXIS_VSI_LIBS + '/' +os.getenv('UVMC_BUILD_PLATFORM') +'/remote')

LDFLAGS.append('-Wl,--whole-archive')
LDFLAGS.append(XL_VIP_LIBS+'/xl_vip_open_kit_extras.so')
LDFLAGS.append('-Wl,--no-whole-archive')

INCLUDES.append(XL_VIP_HOME+'/lib')
INCLUDES.append(XL_VIP_HOME+'/lib/lib_remote')
INCLUDES.append(XL_VIP_HOME+'/lib/lib_rawc_tlm')
INCLUDES.append(UVMC_HOME+'/src/connect/rawc_tlm')
INCLUDES.append(UVMC_HOME+'/src/connect/rawc_tlm/tlm_rawc')
INCLUDES.append(INNEXIS_VSI_HOME+'/include')

VsiTcpUdpPythonGateway = Extension('VsiTcpUdpPythonGateway',
                    include_dirs = INCLUDES,
                    libraries = LIBRARIES,
                    library_dirs = LIBRARY_DIRS,
                    sources = ["VsiTcpUdpPythonGateway.cxx"],
                    extra_link_args = LDFLAGS)

VsiCanPythonGateway = Extension('VsiCanPythonGateway',
                    include_dirs = INCLUDES,
                    libraries = LIBRARIES,
                    library_dirs = LIBRARY_DIRS,
                    sources = ["VsiCanPythonGateway.cxx"],
                    extra_link_args = LDFLAGS,extra_compile_args=["-Wno-error=format-security"])  

VsiCanFdPythonGateway = Extension('VsiCanFdPythonGateway',
                    include_dirs = INCLUDES,
                    libraries = LIBRARIES,
                    library_dirs = LIBRARY_DIRS,
                    sources = ["VsiCanFdPythonGateway.cxx"],
                    extra_link_args = LDFLAGS)

VsiCommonPythonApi = Extension('VsiCommonPythonApi',
                    include_dirs = INCLUDES,
                    libraries = LIBRARIES,
                    library_dirs = LIBRARY_DIRS,
                    sources = ["VsiCommonPythonApi.cxx"],
                    extra_link_args = LDFLAGS)

VsiLinMasterPythonGateway = Extension('VsiLinMasterPythonGateway',
                    include_dirs = INCLUDES,
                    libraries = LIBRARIES,
                    library_dirs = LIBRARY_DIRS,
                    sources = ["VsiLinMasterPythonGateway.cxx"],
                    extra_link_args = LDFLAGS)

VsiLinSlavePythonGateway = Extension('VsiLinSlavePythonGateway',
                    include_dirs = INCLUDES,
                    libraries = LIBRARIES,
                    library_dirs = LIBRARY_DIRS,
                    sources = ["VsiLinSlavePythonGateway.cxx"],
                    extra_link_args = LDFLAGS)
ext_modules = [VsiCommonPythonApi]
if '--can-gateway' in sys.argv:
    ext_modules.append(VsiCanPythonGateway)
if '--can-fd-gateway' in sys.argv:
    ext_modules.append(VsiCanFdPythonGateway)
if '--tcp-gateway' in sys.argv:
    ext_modules.append(VsiTcpUdpPythonGateway)
if '--lin-master-gateway' in sys.argv:
    ext_modules.append(VsiLinMasterPythonGateway)
if '--lin-slave-gateway' in sys.argv:
    ext_modules.append(VsiLinSlavePythonGateway)

setup(name = 'GatewayPythonPackage', 
        version = '1.0', 
        ext_modules=ext_modules,
        script_args=['build_ext', '--build-lib', PYTHON_GATEWAYS])

