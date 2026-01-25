static const char* svnid = "@(#) $Id: XlTlmEthSoftSw.cxx 1168 2014-04-30 23:46:35Z jstickle $";
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

#include "ConvertVpiSimTime.h"
#include "uvmc_xl_config.h"
#include "XlEtherPacketSnooper.h"
#include "XlTlmEthSoftSw.h"
#include "XlTlmMonitorConfig.h"

using namespace tlm;
using namespace XlEtherPacketSnooper;

using std::map;

// This helper function defines a crude hash function that lays out the 1st 4
// bytes of a MAC address into a 32 bit integer than adds the last 2 to it.
static uint32_t hashMacAddr( const unsigned char *macAddr ){
    uint32_t hash = *((const uint32_t *)macAddr);
    hash += macAddr[4];
    hash += macAddr[5];
    return hash;
}

//______________________                                      ________________
// class XlTlmEthSoftSw \____________________________________/ johnS 6-12-2020
//----------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Title: class XlTlmEthSoftSw: Theory of operation
// ---------------------------------------------------------------------------

//---------------------------------------------------------
// Method: ::XlTlmEthSoftSw() constructor/destructor
//
// The constructor registers the *::b_transport()* and *::nb_transport_fw()*
// callback methods on each TLM-2.0 target socket (which is a
// ~simple_target_socket_tagged~ convenience socket as defined in the IEEE 1666
// TLM-2.0 standard).
//
// The target sockets are tagged so that incoming calls to the
// *::b_transport()* and *::nb_transport_fw()* handlers can distinguish
// which port index the packets arrive and thereby know to propagate the
// ether frames to every port _except_ that one (see *::b_transport()* and
// *::nb_transport_fw()* methods below.
//---------------------------------------------------------

// (begin inline source)

XlTlmEthSoftSw::XlTlmEthSoftSw( sc_module_name name, unsigned numPorts )
  : sc_module( name ),
    dNumPorts( numPorts ),
    isMonitoringEnabled( false )
{
    for( unsigned i=0; i<numPorts; i++ ) {
        char name[128];
        sprintf( name, "rxPort%d", i );
        rxPorts.push_back(
            new tlm_utils::simple_target_socket_tagged<XlTlmEthSoftSw, 32> (
                name ) );

        sprintf( name, "txPort%d", i );
        txPorts.push_back(
            new tlm_utils::simple_initiator_socket<XlTlmEthSoftSw, 32> (
                name ) );

        sprintf( name, "analysisRxPort%d", i );
        analysisRxPorts.push_back(
            new tlm_analysis_port<tlm::tlm_generic_payload> ( name ) );

        sprintf( name, "analysisTxPort%d", i );
        analysisTxPorts.push_back(
            new tlm_analysis_port<tlm::tlm_generic_payload> ( name ) );

        rxPorts[i]->register_b_transport( this,
            &XlTlmEthSoftSw::b_transport, i);
        rxPorts[i]->register_nb_transport_fw( this,
            &XlTlmEthSoftSw::nb_transport_fw, i);
    }
}

XlTlmEthSoftSw::~XlTlmEthSoftSw(){
    for( unsigned i=0; i<dNumPorts; i++ ){
        delete rxPorts[i];
        delete txPorts[i];
        delete analysisRxPorts[i];
        delete analysisTxPorts[i];
    }
}

// (end inline source)

//---------------------------------------------------------
// Method: ::b_transport()
//
// By nature blocking transport operations can be time consuming whereas
// non-blocking ones are ~instantaneous~, a.k.a. consume 0 simulated time.
//
// Notice the logic and commented code below that deal with the *dMacCache* data
// member. This is a lookup table for cached mappings between MAC address and
// associated switch port. As more source MAC addresses arrive via input (RX)
// ports the switch learns more about how to route frames more intelligently to
// output (TX) ports.
//---------------------------------------------------------

// (begin inline source)

void XlTlmEthSoftSw::b_transport( int rxPortId,
        tlm_generic_payload &trans, sc_time &delay ) {
    // Mutex lock the switch to get automatic arbitration of multiple
    // coincident ::b_transport() calls. See comments above in the
    // <Virtual ether switch arbitration> section.
    dBTransportLock.lock();

    if( isMonitoringEnabled == true ) {
        XlTlmMonitorConfig config;
        tlm_generic_payload monitorTrans;
        char banner[1024];
        monitorTrans.set_extension( &config );
        monitorTrans.set_data_length( trans.get_data_length() );
        monitorTrans.set_data_ptr( trans.get_data_ptr() );
        sprintf( banner, "@%lld ns INFO XlTlmEthSoftSw::b_transport() "
            "RECEIVED on rxPortId=%d name=%s\n",
            convert.timeInNs(), rxPortId, rxPorts[rxPortId]->name() );
        config.setBanner( banner );
        config.setTransportKind( XlTlmMonitorConfig::B_TRANSPORT );
        analysisRxPorts[rxPortId]->write( monitorTrans );
        monitorTrans.clear_extension( &config ); 
    }
    // Let's cache this rxPortId associated with the packet MAC source address
    // if it has not already been done ...
    const EtherPacket *packet = (const EtherPacket *)trans.get_data_ptr();

    unsigned macHash = hashMacAddr( packet->srcAddr );
    map<unsigned, unsigned>::iterator it;

    it = dMacCache.find( macHash );
    if( it != dMacCache.end() )
        // If already present it better match !
        assert( (uint32_t)rxPortId == it->second );
    else
        dMacCache[macHash] = rxPortId;

    // Now let's see if we can intelligently send out only to the port
    // associated with the MAC destination address, assuming it has already
    // been learned.
    macHash = hashMacAddr( packet->destAddr );

    it = dMacCache.find( macHash );

    // if( we found a matching port for the MAC address )
    //     Just propgate to that port ...
    if( it != dMacCache.end() ){

        uint32_t portId = it->second;

        if( isMonitoringEnabled == true )
            fprintf( stdout, "@%lld ns INFO XlTlmEthSoftSw::b_transport() "
                "SENDING on txPortId=%d name=%s ...\n",
                convert.timeInNs(), portId, txPorts[portId]->name() );

        (*txPorts[portId])->b_transport( trans, delay );
    }
    else
        // Else case we just propagate the packet to all TX ports other than
        // the RX source one.
        for( unsigned i=0; i<dNumPorts; i++ ){
            if( i != (unsigned)rxPortId ){

                if( isMonitoringEnabled == true ) {
                    XlTlmMonitorConfig config;
                    tlm_generic_payload monitorTrans;
                    char banner[1024];
                    monitorTrans.set_extension( &config );
                    monitorTrans.set_data_length( trans.get_data_length() );
                    monitorTrans.set_data_ptr( trans.get_data_ptr() );
                    sprintf( banner, "@%lld ns INFO XlTlmEthSoftSw::b_transport() "
                        "SENDING on txPortId=%d name=%s ...\n",
                        convert.timeInNs(), i, txPorts[i]->name() );
                    config.setBanner( banner );
                    config.setTransportKind( XlTlmMonitorConfig::B_TRANSPORT );
                    analysisTxPorts[i]->write( monitorTrans );
                    monitorTrans.clear_extension( &config ); 
                }

                (*txPorts[i])->b_transport( trans, delay );

                if( trans.get_response_status() != TLM_OK_RESPONSE )
                    break;
            }
        }
    dBTransportLock.unlock();

    // Allow currently queued up requestors priority access to the switch
    // over this caller in case this caller immediately requests another
    // lock (i.e. while looping in the same testbench thread).
    wait(0, SC_PS);
}
// (end inline source)

//---------------------------------------------------------
// Method: ::nb_transport_fw()
//
// In the non-blocking transport usage, TX ethernet frames can be initiated
// using ~fire-and-forget~ semantics.
//
// Eventually they will be presumed to have completed at the locations of the
// TX ethernet frame recipients depending on whether virtual or RTL in nature.
//
// Because this is a 0-time TX operation, no ~stacked-process~ arbitration
// using mutex-locking is needed as in the case of *::b_transport()*.
//
// Rather, all simultaneous TX ether frame transmissions will either be
// completed in 0-time by recipients which are virtual network nodes processing
// these TX frames, or will be queued in 0-time but will not complete in this
// function call.
//
// See comments for *::b_transport()* about how intelligent packet routing is
// done by using the *dMacCache* data member to cache MAC-to-port mappings.
//---------------------------------------------------------

// (begin inline source)

tlm_sync_enum XlTlmEthSoftSw::nb_transport_fw( int rxPortId,
    tlm_generic_payload &trans,
    tlm_phase &phase, sc_time &delay )
{
    tlm_sync_enum ret = TLM_COMPLETED;
    tlm_sync_enum portRet;

    // Innocent until proven guilty.
    trans.set_response_status( TLM_OK_RESPONSE );

    // (end inline source)

    //---------------------------------------------------------
    // Topic: Handling of the "static configuration" extension
    //
    // If the *::transportFw()* function sees that a TLM configuration
    // extension, it means the intiator is sending a *class VtlTlmEtherConfig*
    // object intended for *class VtlTlmEtherDriver* configuration. But this
    // switch is not using the VTL IP and so can just ignore this
    // configuration. Doing this allows us to simply reuse the same set of
    // traffic producers that are used to test the VTL transactor interfaces to
    // the RTL switch model.

    // (begin inline source)
    uvmc::uvmc_xl_config *configExtension;
    trans.get_extension( configExtension );

    if( configExtension ) return ret;
    // (end inline source)

    if( isMonitoringEnabled == true ) {
        XlTlmMonitorConfig config;
        tlm_generic_payload monitorTrans;
        char banner[1024];
        monitorTrans.set_extension( &config );
        monitorTrans.set_data_length( trans.get_data_length() );
        monitorTrans.set_data_ptr( trans.get_data_ptr() );
        sprintf( banner, "@%lld ns INFO XlTlmEthSoftSw::nb_transport_fw() "
            "RECEIVED on rxPortId=%d name=%s\n",
            convert.timeInNs(), rxPortId, rxPorts[rxPortId]->name() );
        config.setBanner( banner );
        config.setTransportKind( XlTlmMonitorConfig::NB_TRANSPORT_FW );
        analysisRxPorts[rxPortId]->write( monitorTrans );
        monitorTrans.clear_extension( &config ); 
    }
    // Let's cache this rxPortId associated with the packet MAC source address
    // if it has not already been done ...
    const EtherPacket *packet = (const EtherPacket *)trans.get_data_ptr();

    unsigned macHash = hashMacAddr( packet->srcAddr );
    map<unsigned, unsigned>::iterator it;

    it = dMacCache.find( macHash );
    if( it != dMacCache.end() )
        // If already present it better match !
        assert( (uint32_t)rxPortId == it->second );
    else
        dMacCache[macHash] = rxPortId;

    // Now let's see if we can intelligently send out only to the port
    // associated with the MAC destination address, assuming it has already
    // been learned.
    macHash = hashMacAddr( packet->destAddr );

    it = dMacCache.find( macHash );

    // if( we found a matching port for the MAC address )
    //     Just propgate to that port ...
    if( it != dMacCache.end() ){

        uint32_t portId = it->second;

        if( isMonitoringEnabled == true ) {
            XlTlmMonitorConfig config;
            tlm_generic_payload monitorTrans;
            char banner[1024];
            monitorTrans.set_extension( &config );
            monitorTrans.set_data_length( trans.get_data_length() );
            monitorTrans.set_data_ptr( trans.get_data_ptr() );
            sprintf( banner, "@%lld ns INFO XlTlmEthSoftSw::nb_transport_fw() "
                "SENDING on txPortId=%d name=%s ...\n",
                convert.timeInNs(), portId, txPorts[portId]->name() );
            config.setBanner( banner );
            config.setTransportKind( XlTlmMonitorConfig::NB_TRANSPORT_FW );
            analysisTxPorts[portId]->write( monitorTrans );
            monitorTrans.clear_extension( &config ); 
        }

        phase = BEGIN_REQ;
        ret = (*txPorts[portId])->nb_transport_fw( trans, phase, delay );
   }
    else
        // Else case we just propagate the packet to all TX ports other than
        // the RX source one.
        for( unsigned i=0; i<dNumPorts; i++ ){
            if( i != (unsigned)rxPortId ){

                if( isMonitoringEnabled == true ) {
                    XlTlmMonitorConfig config;
                    tlm_generic_payload monitorTrans;
                    char banner[1024];
                    monitorTrans.set_extension( &config );
                    monitorTrans.set_data_length( trans.get_data_length() );
                    monitorTrans.set_data_ptr( trans.get_data_ptr() );
                    sprintf( banner, "@%lld ns INFO XlTlmEthSoftSw::nb_transport_fw() "
                        "SENDING on txPortId=%d name=%s ...\n",
                        convert.timeInNs(), i, txPorts[i]->name() );
                    config.setBanner( banner );
                    config.setTransportKind( XlTlmMonitorConfig::NB_TRANSPORT_FW );
                    analysisTxPorts[i]->write( monitorTrans );
                    monitorTrans.clear_extension( &config ); 
                }

                phase = BEGIN_REQ;
                portRet = (*txPorts[i])->nb_transport_fw( trans, phase, delay );

                if( portRet != TLM_COMPLETED ) {
                    ret = portRet;
                    break;
                }
                if( trans.get_response_status() != TLM_OK_RESPONSE )
                    break;
            }
        }
    return ret;
}
