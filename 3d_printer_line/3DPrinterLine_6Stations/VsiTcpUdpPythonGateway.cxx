#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <VsiCommon.h>
#include "VsiTcpUdpGateway.h"
#include <vector>

using namespace XlEtherPacketSnooper;

static VsiTcpUdpGateway *ethGateway = NULL;
static bool gotRxEthernetPacket = false;
static std::map<uint16_t, std::vector<uint8_t>> receivedEthPacketMap;
static std::map<uint16_t, bool> isEthPacketReceived;
static std::map<uint16_t, uint16_t> destPortNumberMap;
static std::map<uint16_t, uint64_t> numberOfBytesMap;


//---------------------------------------------------------
// Method:rxFrameHandler()
// the rxFrameHandler is the callback function that we register 
// in the VsiTcpUdpGateway, when it's called, we save the received
// payload, destination, source port numbers and the
// number of bytes received in static global variables and
// we set the gotRxEthernetPacket flag
//---------------------------------------------------------
static void rxFrameHandler(unsigned char *payload, unsigned numBytes, unsigned short destPortNum, unsigned short srcPortNum, void *context)
{
    if(receivedEthPacketMap.find(srcPortNum) == receivedEthPacketMap.end())
    {
        receivedEthPacketMap[srcPortNum]  = std::vector<uint8_t>(ETH_PACKET_MAX_BYTES, 0);
    }

    // This is to adjust the buffer size to accommodate for
    // payload with size > ETH_PACKET_MAX_BYTES
    if(numBytes>receivedEthPacketMap[srcPortNum].size())
        receivedEthPacketMap[srcPortNum].resize(numBytes);
    
    destPortNumberMap[srcPortNum] = destPortNum;
    numberOfBytesMap[srcPortNum] = numBytes;
    isEthPacketReceived[srcPortNum] = true;
    memcpy(receivedEthPacketMap[srcPortNum].data(), payload, numBytes);
}

//---------------------------------------------------------
// Method:recvEthernetPacket()
// Python API that checks the gotRxEthernetPacket flag
// and if it's true, it returns the received payload,
// the source and the destination port numbers
// and the number of bytes received
//---------------------------------------------------------
static PyObject* recvEthernetPacket(PyObject* self, PyObject* args) 
{
    uint16_t srcPortNum;  
    if(!PyArg_ParseTuple(args, "H", &srcPortNum))
        return NULL;
    
    if(receivedEthPacketMap.find(srcPortNum) == receivedEthPacketMap.end())
    {
        receivedEthPacketMap[srcPortNum]  = std::vector<uint8_t>(ETH_PACKET_MAX_BYTES, 0);
    }

    if(isEthPacketReceived[srcPortNum] && (receivedEthPacketMap.find(srcPortNum) != receivedEthPacketMap.end()))
    {
        isEthPacketReceived[srcPortNum] = false;
        return Py_BuildValue ("HHy#K", destPortNumberMap[srcPortNum], srcPortNum, receivedEthPacketMap[srcPortNum].data(), static_cast<size_t>(numberOfBytesMap[srcPortNum]),  numberOfBytesMap[srcPortNum]);
    }
    else
    {
        return Py_BuildValue ("HHy#K", destPortNumberMap[srcPortNum], srcPortNum, receivedEthPacketMap[srcPortNum].data(), numberOfBytesMap[srcPortNum], numberOfBytesMap[srcPortNum]);
    }
}



//---------------------------------------------------------
// Method:terminate()
// Python API that terminates the Ethernet gateway's
// connection
//---------------------------------------------------------
static PyObject* terminate(PyObject* self, PyObject* args) 
{
    int terminated = 1;
    try 
    { 
        ethGateway->terminate(); 
    }
    catch (...) 
    {
        terminated = 0;
    }
    return Py_BuildValue("i", terminated);
}

//---------------------------------------------------------
// Method:destruct()
// Destructor for the Python module
//---------------------------------------------------------
void destruct(void *module) 
{
    if (ethGateway)
    {
        delete ethGateway;
    }
}



//---------------------------------------------------------
// Method:isTerminationOnGoing()
// Python API that checks whether the Ethernet gateway
// is terminating at the moment
//---------------------------------------------------------
static PyObject* isTerminationOnGoing(PyObject* self, PyObject* args) 
{
    return Py_BuildValue("i", ethGateway->isTerminationOnGoing());
}

static PyObject* isTerminated(PyObject* self, PyObject* args) 
{
    return Py_BuildValue("i", ethGateway->isTerminated());
}

//---------------------------------------------------------
// Method:initialize()
// Python API that parses the RawcTlmApiThreaded instance and
// initializes the VsiTcpEthernet gateway
//---------------------------------------------------------
static PyObject* initialize(PyObject* self, PyObject* args) 
{
    unsigned int conduitId;
    Py_buffer macAddress;
    Py_buffer ipAddress;
    PyObject* capsule;

    if(!PyArg_ParseTuple(args, "Oiy*y*", &capsule, &conduitId, &macAddress, &ipAddress))
        return NULL;

    RawcTlmApiThreaded* dSession = static_cast<RawcTlmApiThreaded*>(PyCapsule_GetPointer(capsule, nullptr));
    string conduitIdString = to_string(conduitId);
    string rxEtherFrameConduit = string(":rxEtherFrameConduit") + conduitIdString;
    string txEtherFrameConduit = string(":txEtherFrameConduit") + conduitIdString;
    
    ethGateway = new VsiTcpUdpGateway(dSession, txEtherFrameConduit.c_str(), rxEtherFrameConduit.c_str(), 
                                   (unsigned char*)macAddress.buf, (unsigned char*)ipAddress.buf);
    ethGateway->registerRxCallback(NULL, rxFrameHandler);

    return Py_BuildValue("i", 0);
}

//---------------------------------------------------------
// Method:tcpListen()
// Python API that parses the required arguments for calling
// TCP's tcpListen() method
//---------------------------------------------------------
static PyObject* tcpListen(PyObject* self, PyObject* args) 
{
    unsigned int serverPortNumber;
    if(!PyArg_ParseTuple(args, "H", &serverPortNumber))
        return NULL;

    uint16_t clientPortNumber;
    clientPortNumber = ethGateway->tcpListen(serverPortNumber);
    if(clientPortNumber == 0)
        VSI_ERROR(MSG_VSI_ETH_GATEWAY, "Failed to connect to TCP port!\n");

    return Py_BuildValue("H", clientPortNumber);
}

//---------------------------------------------------------
// Method:udpBind()
// Python API that parses the required arguments for calling
// UDP's udpBind() method
//---------------------------------------------------------
static PyObject* udpBind(PyObject* self, PyObject* args) 
{
    unsigned int serverPortNumber;
    if(!PyArg_ParseTuple(args, "H", &serverPortNumber))
        return NULL;

    uint16_t clientPortNumber;
    clientPortNumber = ethGateway->udpBind(serverPortNumber);
    if(clientPortNumber == 0)
        VSI_ERROR(MSG_VSI_ETH_GATEWAY, "Failed to connect to UDP port!\n");

    return Py_BuildValue("H", clientPortNumber);
}

//---------------------------------------------------------
// Method:tcpConnect()
// Python API that parses the required arguments for calling
// TCP's tcpConnect() method
//---------------------------------------------------------
static PyObject* tcpConnect(PyObject* self, PyObject* args) 
{
    unsigned short serverPortNumber;
    unsigned short clientPortNumber;
    Py_buffer destIpAddress;
    if(!PyArg_ParseTuple(args, "y*H", &destIpAddress, &serverPortNumber))
        return NULL;

    clientPortNumber = ethGateway->tcpConnect((unsigned char *)destIpAddress.buf, serverPortNumber, 0);
    if(clientPortNumber == 0)
        VSI_ERROR(MSG_VSI_ETH_GATEWAY, "Failed to connect to TCP port!\n");
    
    return Py_BuildValue("H", clientPortNumber);
}

//---------------------------------------------------------
// Method:udpConnect()
// Python API that parses the required arguments for calling
// UDP's udpConnect() method
//---------------------------------------------------------
static PyObject* udpConnect(PyObject* self, PyObject* args) 
{
    unsigned short serverPortNumber;
    unsigned short clientPortNumber;
    Py_buffer destIpAddress;
    if(!PyArg_ParseTuple(args, "y*H", &destIpAddress, &serverPortNumber))
        return NULL;

    clientPortNumber = ethGateway->udpConnect((unsigned char *)destIpAddress.buf, serverPortNumber, 0);
    if(clientPortNumber == 0)
        VSI_ERROR(MSG_VSI_ETH_GATEWAY, "Failed to connect to UDP port!\n");
    
    return Py_BuildValue("H", clientPortNumber);
}

//---------------------------------------------------------
// Method:sendEthernetPacket()
// Python API that parses the the destination client port
// number, the payload and the payload length to 
// send it through the ethernet gateway
//---------------------------------------------------------
static PyObject* sendEthernetPacket(PyObject* self, PyObject* args) 
{
    unsigned short clientPortNum;
    Py_buffer payload;
    if(!PyArg_ParseTuple(args, "Hy*", &clientPortNum, &payload))
        return NULL;
    ethGateway->sendEthernetPacket(clientPortNum, (unsigned char *)payload.buf, (unsigned int)payload.len);

    return Py_BuildValue("i", 1);
}

//---------------------------------------------------------
// The module's function definition struct
//---------------------------------------------------------
static PyMethodDef clientMethods[] = 
{
    { "initialize", initialize, METH_VARARGS, "Connect to TLM fabric server" },
    { "recvEthernetPacket", recvEthernetPacket, METH_VARARGS, "Receive Ethernet frames from TLM fabric server" },
    { "sendEthernetPacket", sendEthernetPacket, METH_VARARGS, "Send Ethernet frames to TLM fabric server" },
    { "tcpConnect", tcpConnect, METH_VARARGS},
    { "udpConnect", udpConnect, METH_VARARGS},
    { "tcpListen", tcpListen, METH_VARARGS},
    { "udpBind", udpBind, METH_VARARGS},
    { "terminate", terminate, METH_VARARGS},
    { "isTerminationOnGoing", isTerminationOnGoing, METH_VARARGS},
    { "isTerminated", isTerminated, METH_VARARGS},
    { NULL, NULL, 0, NULL }
};

static struct PyModuleDef VsiTcpUdpPythonGateway = 
{
    PyModuleDef_HEAD_INIT,
    "VsiTcpUdpPythonGateway",
    "C Client Module",
    -1,
    clientMethods,
    NULL,
    NULL,
    NULL,
    destruct
};

PyMODINIT_FUNC PyInit_VsiTcpUdpPythonGateway(void) 
{
    return PyModule_Create(&VsiTcpUdpPythonGateway);
}
