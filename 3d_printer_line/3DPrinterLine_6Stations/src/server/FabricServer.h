//----------------------------------------------------------------------------//
// Unpublished work. Copyright 2023 Siemens                                   //
//                                                                            //
// This material contains trade secrets or otherwise confidential             //
// information owned by Siemens Industry Software Inc. or its affiliates      //
// (collectively, "SISW"), or its licensors. Access to and use of this        //
// information is strictly limited as set forth in the Customer's applicable  //
// agreements with SISW.                                                      //
//----------------------------------------------------------------------------//

#if defined WIN32 || defined _WIN32
#include <WinSock2.h>
#else
#include <arpa/inet.h>
#endif

#include <systemc.h>
#include "uvmc.h" 
#include "VsiSimCtrl.h"

#include "XlRemoteTlmConduitPkg.h"
#include "XlSyscTlmTimeServer.h"
#include "XlSyscTlmResetServer.h"

#include "XlTlmEthSoftSw.h"
#include "VsiEthMonitor.h"

using namespace uvmc;

#define NUMBER_OF_CLIENTS 7
#define RESET_NS 100

// Attributes that would be used to connect to the server
const char *DEFAULT_SERVER_URL = "localhost";
unsigned domain = AF_UNIX; 
unsigned portNum = 50101;
string commandFilePath = "";
#define NUMBER_OF_ETH_PORTS 7

// Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions

// End of user custom code region. Please don't edit beyond this point.

// ---------------------------------------------------------------------------
// Title: class FabricServer: Top level SC_MODULE on TLM fabric server side
// ---------------------------------------------------------------------------
// The ~class FabricServer~ is the top-level SC_MODULE of the HVL-side
// testbench.
// This module instantiates all the components shown on the server-side.
// ---------------------------------------------------------------------------

class FabricServer: public sc_module {
	private:
		XlSyscTlmTimeServer * timeServer[NUMBER_OF_CLIENTS];
		XlSyscTlmResetServer * resetServer;
		VsiSimCtrl * vsiSimCtrl;
		bool userEnteredExitCommand;

        void errorOnTransport(const char *functionName, int line, const char *file, const char* moduleName) const
        {   
            char messageBuffer[1024];
            sprintf(messageBuffer, "Error on transport socket '%s' [line #%d of '%s'].\n",
                functionName, line, file);
            SC_REPORT_FATAL(moduleName, messageBuffer); 
        }

		XlTlmEthSoftSw *softSwitch;
		VsiEthMonitor *vsiEthMonitor;
		SC_HAS_PROCESS(FabricServer);

		// Start of user custom code region. Please apply edits only within these regions:  Private class members

		// End of user custom code region. Please don't edit beyond this point.

	public:
		FabricServer (sc_module_name name);
		~FabricServer();
		void mainThread ();
		void start_of_simulation ();

		// Start of user custom code region. Please apply edits only within these regions:  Public class members

		// End of user custom code region. Please don't edit beyond this point.

};
