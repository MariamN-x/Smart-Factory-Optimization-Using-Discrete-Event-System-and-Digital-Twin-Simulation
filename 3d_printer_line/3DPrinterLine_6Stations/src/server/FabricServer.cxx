//----------------------------------------------------------------------------//
// Unpublished work. Copyright 2023 Siemens                                   //
//                                                                            //
// This material contains trade secrets or otherwise confidential             //
// information owned by Siemens Industry Software Inc. or its affiliates      //
// (collectively, "SISW"), or its licensors. Access to and use of this        //
// information is strictly limited as set forth in the Customer's applicable  //
// agreements with SISW.                                                      //
//----------------------------------------------------------------------------//
#include "FabricServer.h"

// Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions

// End of user custom code region. Please don't edit beyond this point.

// ---------------------------------------------------------------------------
// Method: readArgs()
// This method is responsible for reading the arguments passed to the
// main function
// ---------------------------------------------------------------------------
static int readArgs(int argc, char *argv[], unsigned &domain, string &commandFilePath)
{   
    int i, usage = 0;
    char argName[BUFSIZ];
    char argVal[BUFSIZ];

	// Start of user custom code region. Please apply edits only within these regions:  Read Args Begin

	// End of user custom code region. Please don't edit beyond this point.

    for(i = 1; i < argc && usage == 0; i++) 
    {
        strcpy(argName, "");
        strcpy(argVal, "");
        int numArgs = sscanf(argv[i], "--%[^=]=%s", argName, argVal);

        string arg = argName;
        
        if(arg == "domain") 
        {
            arg = argVal;
            sscanf(argVal, " %d", &domain);

            if(arg == "AF_INET") 
                domain = AF_INET;
            else if(arg == "AF_UNIX") 
                domain = AF_UNIX;
            else usage = 1;
        }
        else if (arg == "cmdFile")
        {
            commandFilePath = argVal;
        }

		// Start of user custom code region. Please apply edits only within these regions:  Read Args for loop

		// End of user custom code region. Please don't edit beyond this point.

        
        else 
        {
            usage = 1;
        }
    }

    if( usage ) 
    {
        printf( "========================================================\n" );
        printf( "Warning:: Invalid command line argument passed\n" );
        printf( "========================================================\n" );
        printf( "usage: FabricServer\n" );
        printf( "    --domain=AF_INET | AF_UNIX    (default: AF_INET)\n" );
        printf( "    --cmdFile=<pathToCmdFile>     (default: "")\n" );
        
    }
    return( usage );
}

// ---------------------------------------------------------------------------
// Constructor 
// 
// Responsible for instantiating a timeServer object for each client,
// a resetServer and an ethernet softswitch.
// It also binds together all the components using UVMC connect.
// ---------------------------------------------------------------------------
FabricServer::FabricServer(sc_module_name name): sc_module(name) 
{
	userEnteredExitCommand = false;
	SC_THREAD(mainThread);

	for (int i =0; i < NUMBER_OF_CLIENTS; i++)
	{
		const char* timeServerConduitId = "timeServer" + char(i);
		timeServer[i] = new XlSyscTlmTimeServer(timeServerConduitId);
	}

	// Instantiate resetServer defining the reset interval
	resetServer = new XlSyscTlmResetServer("resetServer", /*numTargetSockets=*/NUMBER_OF_CLIENTS, /*resetIntervalInNs=*/RESET_NS);
	softSwitch = new XlTlmEthSoftSw("softSwitch", NUMBER_OF_ETH_PORTS);
	vsiEthMonitor = new VsiEthMonitor("VsiEthMonitor");

	// Connect the RX direction of monitor analysis component0
	softSwitch->analysisRxPorts[0]->bind(*vsiEthMonitor);
	// Connect the TX direction of monitor analysis component0
	softSwitch->analysisTxPorts[0]->bind(*vsiEthMonitor);

	// Connect the RX direction of monitor analysis component1
	softSwitch->analysisRxPorts[1]->bind(*vsiEthMonitor);
	// Connect the TX direction of monitor analysis component1
	softSwitch->analysisTxPorts[1]->bind(*vsiEthMonitor);

	// Connect the RX direction of monitor analysis component2
	softSwitch->analysisRxPorts[2]->bind(*vsiEthMonitor);
	// Connect the TX direction of monitor analysis component2
	softSwitch->analysisTxPorts[2]->bind(*vsiEthMonitor);

	// Connect the RX direction of monitor analysis component3
	softSwitch->analysisRxPorts[3]->bind(*vsiEthMonitor);
	// Connect the TX direction of monitor analysis component3
	softSwitch->analysisTxPorts[3]->bind(*vsiEthMonitor);

	// Connect the RX direction of monitor analysis component4
	softSwitch->analysisRxPorts[4]->bind(*vsiEthMonitor);
	// Connect the TX direction of monitor analysis component4
	softSwitch->analysisTxPorts[4]->bind(*vsiEthMonitor);

	// Connect the RX direction of monitor analysis component5
	softSwitch->analysisRxPorts[5]->bind(*vsiEthMonitor);
	// Connect the TX direction of monitor analysis component5
	softSwitch->analysisTxPorts[5]->bind(*vsiEthMonitor);

	// Connect the RX direction of monitor analysis component6
	softSwitch->analysisRxPorts[6]->bind(*vsiEthMonitor);
	// Connect the TX direction of monitor analysis component6
	softSwitch->analysisTxPorts[6]->bind(*vsiEthMonitor);

	// To enable monitoring and updating signal database set it to true
	softSwitch->enableMonitoring(true);

	// Start of user custom code region. Please apply edits only within these regions:  Protocol Setup

	// End of user custom code region. Please don't edit beyond this point.

	vsiSimCtrl = new VsiSimCtrl("vsiSimCtrl", NUMBER_OF_CLIENTS, false, commandFilePath, RESET_NS, "signals");
	vsiSimCtrl->initializeEthSignalsMonitor(vsiEthMonitor);


	// ---------------------------------------------------------------------------
	// Topic: Stitch it all together with UVM-Connect
	//
	// Here we ~UVM-Connect~ all the various TLM-2.0 components of the components
	// mentioned above so as to stitch together the entire TLM fabric
	// portion of the system that is running on this local server process.
	// ---------------------------------------------------------------------------


	// --------------------------------- Component0 ---------------------------------
	resetServer->connect(0, ":resetServerConduit0");

	uvmc_connect <uvmc_xl_converter<tlm_generic_payload> > (timeServer[0]->advanceTargetSocket, ":timeServerConduit0");

	// Connect the RX direction of client0
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->rxPorts[0], ":txEtherFrameConduit0");
	// Connect the TX direction of client0
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->txPorts[0], ":rxEtherFrameConduit0");

	// connect TX direction of the configPort for client0
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->txConfigPorts[0], ":rxConfigPort0");
	// connect RX direction of the configPort for client0
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->rxConfigPorts[0], ":txConfigPort0");

	// --------------------------------- Component1 ---------------------------------
	resetServer->connect(1, ":resetServerConduit1");

	uvmc_connect <uvmc_xl_converter<tlm_generic_payload> > (timeServer[1]->advanceTargetSocket, ":timeServerConduit1");

	// Connect the RX direction of client1
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->rxPorts[1], ":txEtherFrameConduit1");
	// Connect the TX direction of client1
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->txPorts[1], ":rxEtherFrameConduit1");

	// connect TX direction of the configPort for client1
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->txConfigPorts[1], ":rxConfigPort1");
	// connect RX direction of the configPort for client1
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->rxConfigPorts[1], ":txConfigPort1");

	// --------------------------------- Component2 ---------------------------------
	resetServer->connect(2, ":resetServerConduit2");

	uvmc_connect <uvmc_xl_converter<tlm_generic_payload> > (timeServer[2]->advanceTargetSocket, ":timeServerConduit2");

	// Connect the RX direction of client2
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->rxPorts[2], ":txEtherFrameConduit2");
	// Connect the TX direction of client2
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->txPorts[2], ":rxEtherFrameConduit2");

	// connect TX direction of the configPort for client2
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->txConfigPorts[2], ":rxConfigPort2");
	// connect RX direction of the configPort for client2
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->rxConfigPorts[2], ":txConfigPort2");

	// --------------------------------- Component3 ---------------------------------
	resetServer->connect(3, ":resetServerConduit3");

	uvmc_connect <uvmc_xl_converter<tlm_generic_payload> > (timeServer[3]->advanceTargetSocket, ":timeServerConduit3");

	// Connect the RX direction of client3
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->rxPorts[3], ":txEtherFrameConduit3");
	// Connect the TX direction of client3
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->txPorts[3], ":rxEtherFrameConduit3");

	// connect TX direction of the configPort for client3
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->txConfigPorts[3], ":rxConfigPort3");
	// connect RX direction of the configPort for client3
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->rxConfigPorts[3], ":txConfigPort3");

	// --------------------------------- Component4 ---------------------------------
	resetServer->connect(4, ":resetServerConduit4");

	uvmc_connect <uvmc_xl_converter<tlm_generic_payload> > (timeServer[4]->advanceTargetSocket, ":timeServerConduit4");

	// Connect the RX direction of client4
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->rxPorts[4], ":txEtherFrameConduit4");
	// Connect the TX direction of client4
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->txPorts[4], ":rxEtherFrameConduit4");

	// connect TX direction of the configPort for client4
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->txConfigPorts[4], ":rxConfigPort4");
	// connect RX direction of the configPort for client4
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->rxConfigPorts[4], ":txConfigPort4");

	// --------------------------------- Component5 ---------------------------------
	resetServer->connect(5, ":resetServerConduit5");

	uvmc_connect <uvmc_xl_converter<tlm_generic_payload> > (timeServer[5]->advanceTargetSocket, ":timeServerConduit5");

	// Connect the RX direction of client5
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->rxPorts[5], ":txEtherFrameConduit5");
	// Connect the TX direction of client5
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->txPorts[5], ":rxEtherFrameConduit5");

	// connect TX direction of the configPort for client5
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->txConfigPorts[5], ":rxConfigPort5");
	// connect RX direction of the configPort for client5
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->rxConfigPorts[5], ":txConfigPort5");

	// --------------------------------- Component6 ---------------------------------
	resetServer->connect(6, ":resetServerConduit6");

	uvmc_connect <uvmc_xl_converter<tlm_generic_payload> > (timeServer[6]->advanceTargetSocket, ":timeServerConduit6");

	// Connect the RX direction of client6
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->rxPorts[6], ":txEtherFrameConduit6");
	// Connect the TX direction of client6
	uvmc_connect <uvmc_xl_converter<tlm_generic_payload>> 
		(*softSwitch->txPorts[6], ":rxEtherFrameConduit6");

	// connect TX direction of the configPort for client6
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->txConfigPorts[6], ":rxConfigPort6");
	// connect RX direction of the configPort for client6
	uvmc_connect < uvmc_xl_converter<tlm_generic_payload> > (*vsiSimCtrl->vsiServerConfigGateway->rxConfigPorts[6], ":txConfigPort6");

	// Start of user custom code region. Please apply edits only within these regions:  UVMC Connect & resetServer Connect

	// End of user custom code region. Please don't edit beyond this point.

}

FabricServer::~FabricServer() 
{
	delete vsiSimCtrl;

	delete resetServer;
	for (int i =0; i < NUMBER_OF_CLIENTS; i++)
	{
		delete timeServer[i];
	}

	delete softSwitch;
	delete vsiEthMonitor;

	// Start of user custom code region. Please apply edits only within these regions:  Destructor

	// End of user custom code region. Please don't edit beyond this point.
}

// ---------------------------------------------------------------------------
// Start of Simulation 
// Responsible for establishing the connection with the clients
// ---------------------------------------------------------------------------
void FabricServer::start_of_simulation()
{
	//Establish connection to 'remoteSession0' client.
	XlRemoteTlmConduit::connectToClient(":remoteSession0", domain, portNum + 0);	

	//Establish connection to 'remoteSession1' client.
	XlRemoteTlmConduit::connectToClient(":remoteSession1", domain, portNum + 1);	

	//Establish connection to 'remoteSession2' client.
	XlRemoteTlmConduit::connectToClient(":remoteSession2", domain, portNum + 2);	

	//Establish connection to 'remoteSession3' client.
	XlRemoteTlmConduit::connectToClient(":remoteSession3", domain, portNum + 3);	

	//Establish connection to 'remoteSession4' client.
	XlRemoteTlmConduit::connectToClient(":remoteSession4", domain, portNum + 4);	

	//Establish connection to 'remoteSession5' client.
	XlRemoteTlmConduit::connectToClient(":remoteSession5", domain, portNum + 5);	

	//Establish connection to 'remoteSession6' client.
	XlRemoteTlmConduit::connectToClient(":remoteSession6", domain, portNum + 6);	

	// Start of user custom code region. Please apply edits only within these regions:  start_of_simulation

	// End of user custom code region. Please don't edit beyond this point.

	XlRemoteTlmConduit::listenForAllClients();

	fprintf(stdout, "+=+ INFO: FabricServer::start_of_simulation() "
                "Waiting for client connections ...\n");
	fflush(stdout);

}

void FabricServer::mainThread()
{
    try 
    { 
        // Ok, now wait for initial reset from reset generator before enabling
        // transaction traffic generation.
        printf("@%lld ns (SC time) +=+ INFO: Awaiting reset ...\n", convert.timeInNs());
        resetServer->waitForReset();
        printf("@%lld ns (SC time)+=+ INFO: ... got it!\n", convert.timeInNs());
        fflush(stdout);
        sc_time delay;
        tlm_phase phase;


		// Start of user custom code region. Please apply edits only within these regions:  After Reset

		// End of user custom code region. Please don't edit beyond this point.

        
        
        userEnteredExitCommand = vsiSimCtrl->simCtrlMainThread();

		// Start of user custom code region. Please apply edits only within these regions:  Before Disconnecting

		// End of user custom code region. Please don't edit beyond this point.

        
		XlRemoteTlmConduit::disconnectFromClient(":remoteSession0");
		XlRemoteTlmConduit::disconnectFromClient(":remoteSession1");
		XlRemoteTlmConduit::disconnectFromClient(":remoteSession2");
		XlRemoteTlmConduit::disconnectFromClient(":remoteSession3");
		XlRemoteTlmConduit::disconnectFromClient(":remoteSession4");
		XlRemoteTlmConduit::disconnectFromClient(":remoteSession5");
		XlRemoteTlmConduit::disconnectFromClient(":remoteSession6");

    }
    catch( string message ) 
    {
        cerr << message << endl;
        cerr << "Fatal Error: Program aborting." << endl;
    }
    catch(sc_report message) 
    {
        cout << "Error: SystemC report:" << endl;
        cout << "Type: "        << message.get_msg_type() << endl;
        cout << "Message: "     << message.get_msg() << endl;
        cout << "Severity: "    << message.get_severity() << endl;
        cout << "Where: line #" << message.get_line_number()
             << " in "          << message.get_file_name() << endl;
        cout << "Fatal Error: Program aborting." << endl;
    }
    catch(sc_exception message) 
    {
        cerr << "Error: SystemC exception:" << endl;
        cerr << message.what() << endl;
        cerr << "Fatal Error: Program aborting." << endl;
    }
    catch(...) 
    {
        cerr << "Error: Unclassified exception." << endl;
        cerr << "Fatal Error: Program aborting." << endl;
    }
}

// ---------------------------------------------------------------------------
// Method: sc_main()
// The top-level ~sc_main()~ entrypoint from the OSCI SystemC kernel is shown
// here.
// ---------------------------------------------------------------------------
int sc_main(int argc, char *argv[]) 
{
    FabricServer *fabricServer;
    int ret = 0;

    if(readArgs(argc, (char **)argv, domain, commandFilePath )) 
        return -1;

    try 
    {
        fabricServer = new FabricServer("fabricServer");
        sc_start();
    }
	catch(string message) 
    {
        cout << message << endl;
        cout << "Fatal Error: Program aborting." << endl;
        ret = -1;
    }
    catch(sc_report message) 
    {
        cout << "Error: SystemC report:" << endl;
        cout << "Type: "        << message.get_msg_type() << endl;
        cout << "Message: "     << message.get_msg() << endl;
        cout << "Severity: "    << message.get_severity() << endl;
        cout << "Where: line #" << message.get_line_number()
             << " in "          << message.get_file_name() << endl;
        cout << "Fatal Error: Program aborting." << endl;
        ret = -1;
    }
    catch(sc_exception message) 
    {
        cout << "Error: SystemC exception:" << endl;
        cout << message.what() << endl;
        cout << "Fatal Error: Program aborting." << endl;
        ret = -1;
    }
    catch(...) 
    {
        cout << "Error: Unclassified exception." << endl;
        cout << "Fatal Error: Program aborting." << endl;
        ret = -1;
    }
    
    if(fabricServer) delete fabricServer;
    return ret;
}
