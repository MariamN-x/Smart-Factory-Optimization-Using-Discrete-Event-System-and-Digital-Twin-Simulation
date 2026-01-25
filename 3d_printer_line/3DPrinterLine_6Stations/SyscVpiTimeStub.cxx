static const char* svnid = "@(#) $Id: XlTlmMessageLayer.cxx 1816 2018-09-25 01:03:34Z jstickle $";
static void* gccnowarn = &gccnowarn + (long)&svnid;

//--------------------------------------------------------------------------//
// Siemens EDA                                                              //
// Unpublished work. (C) Copyright 2021 Siemens                             //
//                                                                          //
// This material contains trade secrets or otherwise confidential           //
// information owned by Siemens Industry Software Inc. or its affiliates    //
// (collectively, "SISW"), or its licensors. Access to and use of this      //
// information is strictly limited as set forth in the Customer's           //
// applicable agreements with SISW.                                         //
//                                                                          //
// This material (reprints of pages, PDFs, complete code examples) may not  //
// be copied, distributed, or otherwise disclosed outside of the Customer’s //
// facilities without the express written permission of SISW, and may not   //
// be used in any way not expressly authorized by SISW.                     //
//--------------------------------------------------------------------------//

#include <stdio.h>
#include <signal.h>
#include <map>
#include "systemc.h"
#include "svdpi.h"
#include "vpi_user.h"

// This code is meant to be used with standalone OSCI SystemC executables
// that might be used to implement a "standalone remote client".
//
// In remote clients we provide this function to emulate actual vpi_get()
// in what is normally a Questa simulation or OSCI front end to a Veloce
// simulation.
//
// The only way vpi_*() calls are used is in the SCEMI compliant way which
// is to only use vpi_get() to retrieve vpiTimePrecision and vpi_get_time()
// to retrieve current time.
//
// See ../ConverVpiSimTime.cxx for more info.
//
// For remote clients we emulate these calls with the functions below.
// Simulation precision is set to PS as part of the "fake out".
//
// Simulation current time simply becomes SystemC's current time.
// transaction from the fabric server.
//
// The following code fragment from ConvertVpiSimTime.cxx shows meanings of
// VPI time precision values:
//
//  unsigned long long ConvertVpiSimTime::precisionConverterForPs[] = {
//      1000000000000LL, //  0    1  s
//      100000000000LL, // -1  100 ms
//      10000000000LL, // -2   10 ms
//      1000000000LL, // -3    1 ms
//      100000000LL, // -4  100 us
//      10000000LL, // -5   10 us
//      1000000LL, // -6    1 us
//      100000LL, // -7  100 ns 
//      10000LL, // -8   10 ns
//      1000LL, // -9    1 ns
//      100LL, // -10 100 ps
//      10LL, // -11  10 ps
//      1LL, // -12   1 ps
//      10LL, // -13  100 fs
//      100LL, // -14  10 fs
//      1000LL  // -15  1 fs
//  };

static PLI_INT32 simulationPrecision = -12; // precision = 1 ps

PLI_INT32 vpi_get( PLI_INT32 property, vpiHandle object ){
    // Required SCEMI usage.
    assert( property==vpiTimePrecision && object == NULL );
    return simulationPrecision;
}

void vpi_get_time( vpiHandle object, p_vpi_time time_p ){
    assert( object == NULL ); // Required SCEMI usage.
    unsigned long long currentTimeInPs = sc_time_stamp().to_seconds()*1e12;
    time_p->low = currentTimeInPs;
    time_p->high = currentTimeInPs >> 32;
}
