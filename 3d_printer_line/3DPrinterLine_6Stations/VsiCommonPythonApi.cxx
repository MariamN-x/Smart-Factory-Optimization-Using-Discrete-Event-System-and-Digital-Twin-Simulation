#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>

#include "ConvertVpiSimTime.h"
#include "VsiPortConfigGateway.h"

RawcTlmApiThreaded *dSession= NULL;
static VsiPortConfigGateway* vsiPortConfigGateway = NULL;

//---------------------------------------------------------
// Method:initializeConnection()
// Function to initialize the RawcTlmApiThreaded instance
// and the VsiPortConfigGateway
//---------------------------------------------------------
int initializeConnection(const char* serverUrl, unsigned int domain, unsigned int portNum, unsigned int conduitId)
{
    string conduitIdString = to_string(conduitId);
    string remoteSession = string(":remoteSession") + conduitIdString;
    string timeServer = string(":timeServerConduit") + conduitIdString;
    string resetServer = string(":resetServerConduit") + conduitIdString;
    string rxEtherFrameConduit = string(":rxEtherFrameConduit") + conduitIdString;
    string txEtherFrameConduit = string(":txEtherFrameConduit") + conduitIdString;
    string rxConfigPort = string(":rxConfigPort") + conduitIdString;
    string txConfigPort = string(":txConfigPort") + conduitIdString;
    
    dSession = new RawcTlmApiThreaded(serverUrl, domain, portNum, remoteSession.c_str(), timeServer.c_str(), resetServer.c_str());
    vsiPortConfigGateway = new VsiPortConfigGateway(dSession, txConfigPort.c_str(), rxConfigPort.c_str());

    cout << "+=+ INFO: ... successfully connected to TLM FabricServer !" << endl;
    return 0;
}

//---------------------------------------------------------
// Method:advanceSimulation()
// A python API to call the advanceNs() method, it returns
// 1 in case of success and 0 in case of error
//---------------------------------------------------------
static PyObject* advanceSimulation(PyObject* self, PyObject* args)
{
    unsigned long long int timeInNs = 0;
    if(!PyArg_ParseTuple(args, "I", &timeInNs))
        return NULL;
        
    int advanceSuccessful = 1;
    try 
    {
        dSession->advanceNs(timeInNs);
    }
    catch (...) 
    {
        advanceSuccessful = 0;
    }
    return Py_BuildValue("i", advanceSuccessful);
}

//---------------------------------------------------------
// Method:waitForReset()
// A python API to call the waitForReset() method, it returns
// 1 in case of success and 0 in case of error
//---------------------------------------------------------
static PyObject* waitForReset(PyObject* self, PyObject* args) 
{
    int resetSuccessful = 1;
    try 
    { 
        dSession->waitForReset(); 
    }
    catch (...) 
    {
        resetSuccessful = 0;
    }
    return Py_BuildValue("i", resetSuccessful);
}

//---------------------------------------------------------
// Method:connectToServer()
// A python API that takes the serverIp, domain, port number
// and the conduitId and calls initializeConnection()
// it returns the RawcTlmApiThreaded instance
//---------------------------------------------------------
static PyObject* connectToServer(PyObject* self, PyObject* args) 
{
    char* serverIp;
    char* domainStr;
    int   port;
    unsigned int conduitId;
    unsigned int domain;

    if(!PyArg_ParseTuple(args, "ssii", &serverIp, &domainStr, &port, &conduitId))
        return NULL;

    if (string(domainStr) == "AF_INET")
        domain = AF_INET;
    else
        domain = AF_UNIX;
    
    initializeConnection(serverIp, domain, port, conduitId);
    
    return PyCapsule_New(dSession, nullptr, nullptr);
}

//---------------------------------------------------------
// Method:getSimulationTimeInNs()
// A python API to call the convert.timeInNs() method
// and returns the time in ns
//---------------------------------------------------------
static PyObject* getSimulationTimeInNs(PyObject* self, PyObject* args) 
{
    return Py_BuildValue("K", convert.timeInNs());
}

//---------------------------------------------------------
// Method:getTotalSimulationTime()
// A python API to call vsiPortConfigGateway->getTotalSimulationTime()
// and returns the total simulation time
//---------------------------------------------------------
static PyObject* getTotalSimulationTime(PyObject* self, PyObject* args) 
{
    return Py_BuildValue("K", vsiPortConfigGateway->getTotalSimulationTime());
}

//---------------------------------------------------------
// Method:getSimulationStep()
// A python API to call vsiPortConfigGateway->getSimulationStep()
// and returns the simulation step
//---------------------------------------------------------
static PyObject* getSimulationStep(PyObject* self, PyObject* args) 
{
    return Py_BuildValue("K", vsiPortConfigGateway->getSimulationStep());
}

//---------------------------------------------------------
// Method:isStopRequested()
// A python API to call vsiPortConfigGateway->isStopRequested()
// and returns a boolean that indicates whether there's a
// stop requested or not
//---------------------------------------------------------
static PyObject* isStopRequested(PyObject* self, PyObject* args) 
{
    return Py_BuildValue("K", vsiPortConfigGateway->isStopRequested());
}

//---------------------------------------------------------
// Method:terminate()
// A python API to terminate the connection and free any objects
// and returns a boolean that indicates whether it's successful
// or not
//---------------------------------------------------------
static PyObject* terminate(PyObject* self, PyObject* args) 
{
    int success = 1;
    try 
    {
        delete vsiPortConfigGateway;
        delete dSession;
    }
    catch (...) 
    {
        success = 0;
    }
    return Py_BuildValue("i", success);
}



//---------------------------------------------------------
// Method:destruct()
// Destructor for the Python module
//---------------------------------------------------------
void destruct(void *module) 
{

    // Free any allocated resources
    delete vsiPortConfigGateway;
    delete dSession;
}



//---------------------------------------------------------
// The module's function definition struct
//---------------------------------------------------------
static PyMethodDef clientMethods[] = 
{
    { "connectToServer", connectToServer, METH_VARARGS, "Connect to TLM fabric server" },
    { "advanceSimulation", advanceSimulation, METH_VARARGS, "Sync with fabric server RtosInterrupt" },
    { "waitForReset", waitForReset, METH_VARARGS, "Sync with fabric server reset" },
    { "getSimulationTimeInNs", getSimulationTimeInNs, METH_VARARGS, "Get TLM fabric server simulation time" },
    { "getTotalSimulationTime", getTotalSimulationTime, METH_VARARGS},
    { "getSimulationStep", getSimulationStep, METH_VARARGS},
    { "isStopRequested", isStopRequested, METH_VARARGS},
    { "terminate", terminate, METH_VARARGS},
    { NULL, NULL, 0, NULL }
};

static struct PyModuleDef VsiCommonPythonApi = 
{
    PyModuleDef_HEAD_INIT,
    "VsiCommonPythonApi",
    "C Client Module",
    -1,
    clientMethods,
    NULL,
    NULL,
    NULL,
    destruct
};

PyMODINIT_FUNC PyInit_VsiCommonPythonApi(void) 
{
    return PyModule_Create(&VsiCommonPythonApi);
}
