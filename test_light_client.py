"""
TO DO:
- [ ] handle clock
"""
from utils.specs import (
    Root, LightClientBootstrap, initialize_light_client_store, compute_sync_committee_period_at_slot,
    get_current_slot, uint64, config, process_light_client_update, SLOTS_PER_EPOCH, MAX_REQUEST_LIGHT_CLIENT_UPDATES,
    LightClientOptimisticUpdate, process_light_client_optimistic_update, process_light_client_finality_update,
    LightClientFinalityUpdate, EPOCHS_PER_SYNC_COMMITTEE_PERIOD, BLSPubkey, BLSSignature)

import time

import utils.parsing as parsing

import requests

import math

import asyncio

from utils.clock import get_current_slot, compute_sync_period_at_slot, compute_epoch_at_slot, compute_sync_period_at_epoch

# Takes into account possible clock drifts. The low value provied protection against a server sending updates too far in the future
MAX_CLOCK_DISPARITY_SEC = 10

OPTIMISTIC_UPDATE_POLL_INTERVAL = 12
FINALITY_UPDATE_POLL_INTERVAL = 48 # Da modificare

LOOKAHEAD_EPOCHS_COMMITTEE_SYNC = 8

ENDPOINT_NODE_URL = "https://lodestar-mainnet.chainsafe.io"

genesis_validators_root = '0xcf8e0d4e9587369b2301d0790347320302cc0943d5a1884560367e8208d920f2' # sostituire usando /eth1/v1/beacon/genesis


def time_until_next_epoch():
    millis_per_epoch = int(SLOTS_PER_EPOCH) * int(config.SECONDS_PER_SLOT) * 1000
    millis_from_genesis = round(time.time_ns() // 1_000_000) - int(config.MIN_GENESIS_TIME) * 1000

    if millis_from_genesis >= 0:
        return millis_per_epoch - (millis_from_genesis % millis_per_epoch)
    else:
        return abs(millis_from_genesis % millis_per_epoch)


def beacon_api(url):
    response = requests.get(url)
    assert response.ok
    return response.json()


def updates_for_period(sync_period, count):
    sync_period = str(sync_period) 
    return beacon_api(f"{ENDPOINT_NODE_URL}/eth/v1/beacon/light_client/updates?start_period={sync_period}&count={count}")


def get_trusted_block_root():
    return Root(parsing.hex_to_bytes(beacon_api(f"{ENDPOINT_NODE_URL}/eth/v1/beacon/headers/finalized")['data']['root']))


def get_light_client_bootstrap(trusted_block_root):
    response = beacon_api(f"{ENDPOINT_NODE_URL}/eth/v1/beacon/light_client/bootstrap/{trusted_block_root}")['data']
    
    return LightClientBootstrap(
        header = parsing.parse_header(response['header']),
        current_sync_committee = parsing.parse_sync_committee(response['current_sync_committee']),
        current_sync_committee_branch = response['current_sync_committee_branch']
        #current_sync_committee_branch = parsing.parse_current_sync_committee_branch(response['current_sync_committee_branch'])
    )


def bootstrap():
    trusted_block_root = get_trusted_block_root()

    light_client_bootstrap = get_light_client_bootstrap(trusted_block_root)
    
    light_client_store = initialize_light_client_store(trusted_block_root, light_client_bootstrap)

    return light_client_store


def chunkify_range(from_period, to_period, items_per_chunk):
    if items_per_chunk < 1:
        items_per_chunk = 1
    
    total_items = to_period - from_period + 1

    chunk_count = max(math.ceil(int(total_items) / items_per_chunk), 1)

    chunks = []
    for i in range(chunk_count):
        _from = from_period + i * items_per_chunk
        _to = min(from_period + (i + 1) * items_per_chunk - 1, to_period)
        chunks.append([_from, _to])
        if _to >= to_period:
            break
    return chunks


def get_optimistic_update():
    optimistic_update = beacon_api(f"{ENDPOINT_NODE_URL}/eth/v1/beacon/light_client/optimistic_update")['data']

    return LightClientOptimisticUpdate(
        attested_header = parsing.parse_header(optimistic_update['attested_header']),
        sync_aggregate = parsing.parse_sync_aggregate(optimistic_update['sync_aggregate']),
        signature_slot = int(optimistic_update['signature_slot'])
    )


# !!! eth1/v1/events allows to subscribe to events
async def handle_optimistic_updates(light_client_store):
    last_optimistic_update = None
    while True:
        try:
            optimistic_update = get_optimistic_update()
            
            if last_optimistic_update is None or last_optimistic_update.attested_header.beacon.slot != optimistic_update.attested_header.beacon.slot:
                last_optimistic_update = optimistic_update
                print("Processing optimistic update: slot", optimistic_update.attested_header.beacon.slot)
                process_light_client_optimistic_update(light_client_store,
                                                    optimistic_update, 
                                                    get_current_slot(tolerance=MAX_CLOCK_DISPARITY_SEC),
                                                    genesis_validators_root)
        except AssertionError: 
            print("Unable to retrieve optimistic update")

        await asyncio.sleep(OPTIMISTIC_UPDATE_POLL_INTERVAL)


def get_finality_update():
    finality_update = beacon_api(f"{ENDPOINT_NODE_URL}/eth/v1/beacon/light_client/finality_update")['data']

    return LightClientFinalityUpdate(
        attested_header = parsing.parse_header(finality_update['attested_header']),
        finalized_header = parsing.parse_header(finality_update['finalized_header']),
        finality_branch = finality_update['finality_branch'],
        sync_aggregate = parsing.parse_sync_aggregate(finality_update['sync_aggregate']),
        signature_slot = int(finality_update['signature_slot'])
    )

async def handle_finality_updates(light_client_store):
    last_finality_update = None
    while True:
        try:
            finality_update = get_finality_update()
            if last_finality_update is None or last_finality_update.finalized_header.beacon.slot != finality_update.finalized_header.beacon.slot:
                last_finality_update = finality_update
                print("Processing finality update: slot", last_finality_update.finalized_header.beacon.slot)
                process_light_client_finality_update(light_client_store,
                                                        finality_update, 
                                                        get_current_slot(tolerance=MAX_CLOCK_DISPARITY_SEC),
                                                        genesis_validators_root)
        except AssertionError:
            print("Unable to retrieve finality update")

        await asyncio.sleep(FINALITY_UPDATE_POLL_INTERVAL)


def sync(light_client_store, last_period, current_period):
    # split the period range into chunks of MAX_REQUEST_LIGHT_CLIENT_UPDATES
    period_ranges = chunkify_range(last_period, current_period, MAX_REQUEST_LIGHT_CLIENT_UPDATES)

    for (from_period, to_period) in period_ranges:
        count = to_period + 1 - from_period
        updates = updates_for_period(from_period, count)
        updates = parsing.parse_light_client_updates(updates)
        for update in updates:
            print("Processing update")
            process_light_client_update(light_client_store, update, get_current_slot(tolerance=MAX_CLOCK_DISPARITY_SEC), genesis_validators_root)


async def main():
    print("Processing bootstrap")
    light_client_store = bootstrap()
    print("Processing bootstrap done")

    print("Start syncing")
    # Compute the current sync period
    current_period = compute_sync_period_at_slot(get_current_slot()) # ! cambia con funzioni di mainnet
        
    # Compute the sync period associated with the optimistic header
    last_period = compute_sync_period_at_slot(light_client_store.optimistic_header.beacon.slot)

    # SYNC
    sync(light_client_store, last_period, current_period)
    print("Sync done")
    
    # subscribe
    print("Start optimistic update handler")
    asyncio.create_task(handle_optimistic_updates(light_client_store))
    print("Start finality update handler")
    asyncio.create_task(handle_finality_updates(light_client_store))

    while True:
        # ! evaluate to insert an optimistic update

        # when close to the end of a sync period poll for sync committee updates
        current_epoch = compute_epoch_at_slot(get_current_slot())
        epoch_in_sync_period = current_epoch % EPOCHS_PER_SYNC_COMMITTEE_PERIOD

        if(EPOCHS_PER_SYNC_COMMITTEE_PERIOD - epoch_in_sync_period <= LOOKAHEAD_EPOCHS_COMMITTEE_SYNC):
            period = compute_sync_period_at_epoch(current_epoch)
            sync(period, period)
        
        print("Polling next sync committee update in", time_until_next_epoch(), " secs")
        await asyncio.sleep(time_until_next_epoch())


from py_ecc.bls import G2ProofOfPossession as py_ecc_bls

if __name__ == "__main__":   
    # assert py_ecc_bls.FastAggregateVerify([
    #         bytes.fromhex("9430d1b204d81f494dac81a714301774ffae9ca734bce404d49ae84431407c2e97bbadac364ac01b4e5ac92518c4e519"),
    #         bytes.fromhex("b26d78658b3e58df82e8204d8caebce7ee9ba16fd95efed86d5d850764fddb04e537c63b95da0f708df8ade98aa2e9c4"),
    #         bytes.fromhex("a50b73c292caf94fc77943ebdfd10cc785423316a89d5c8fc6995540e85e020edf05aee1f29b2475dca177caf995111d")
    #     ],
    #     bytes.fromhex("be2923c5660aedf3ba2ca6fe0f0505f64682bb1d8fcc35383aac93ee0b933b05"),
    #     bytes.fromhex("b5ebaccbf736c6a967502b15abc3ad3ab83604011e6e82ea8fa0e852380a387ae0d3dcf07e01446dcdce580643830cc105178db78845afb76e6ca29b95aeadfb724ae54582eb9d20461cd2b4612e9dd20c33091e6b6bb9b7b2a954f9885f0ac7")
    # )

    # my_hex = "d5722733abc981a2e933beb7b1d306ba201e6b3309e44f859a30ab45d85f6669"
    # my_bytes = bytes.fromhex(my_hex)
    # print(my_bytes)

    # print(bytes.fromhex("a73eb991aa22cdb794da6fcde55a427f0a4df5a4a70de23a988b5e5fc8c4d844f66d990273267a54dd21579b7ba6a086"))
    # assert py_ecc_bls.FastAggregateVerify([
	# 		BLSPubkey("0xa73eb991aa22cdb794da6fcde55a427f0a4df5a4a70de23a988b5e5fc8c4d844f66d990273267a54dd21579b7ba6a086"),
	# 		BLSPubkey("0xb29043a7273d0a2dbc2b747dcf6a5eccbd7ccb44b2d72e985537b117929bc3fd3a99001481327788ad040b4077c47c0d"),
	# 		BLSPubkey("0xb928f3beb93519eecf0145da903b40a4c97dca00b21f12ac0df3be9116ef2ef27b2ae6bcd4c5bc2d54ef5a70627efcb7"),
	# 		BLSPubkey("0x9446407bcd8e5efe9f2ac0efbfa9e07d136e68b03c5ebc5bde43db3b94773de8605c30419eb2596513707e4e7448bb50"),
	# 	],
	# 	bytes.fromhex("0x69241e7146cdcc5a5ddc9a60bab8f378c0271e548065a38bcc60624e1dbed97f"),
	# 	BLSSignature("0xb204e9656cbeb79a9a8e397920fd8e60c5f5d9443f58d42186f773c6ade2bd263e2fe6dbdc47f148f871ed9a00b8ac8b17a40d65c8d02120c00dca77495888366b4ccc10f1c6daa02db6a7516555ca0665bca92a647b5f3a514fa083fdc53b6e")
	# 	)

    asyncio.run(main())

    # count = 0 
    
    # store_period = compute_sync_committee_period_at_slot(light_client_store.finalized_header.beacon.slot)
    # current_slot =  get_current_slot(uint64(int(time.time())), config.MIN_GENESIS_TIME)
    # current_period = compute_sync_committee_period_at_slot(current_slot) 
  
    # if store_period == current_period: 
    #     update = updates_for_period(store_period)
    #     light_client_update = parsing.parse_light_client_update(update)

    # # process_light_client_update(light_client_store, light_client_update, current_slot, genesis_validators_root)

    # while store_period < current_period:
    #     # Define within while loop to continually get updates on the current time
    #     store_period = compute_sync_committee_period_at_slot(light_client_store.finalized_header.slot)
    #     current_slot =  get_current_slot(uint64(int(time.time())), config.MIN_GENESIS_TIME)
    #     current_period = compute_sync_committee_period_at_slot(current_slot) 

    #     # Store period gets updated within process light client update!
    #     updates = updates_for_period(store_period+count)
    #     # Account for store period being the same during bootstrap and period after. 
    #     if count == 0: 
    #         count += 1
        
    #     light_client_update = parsing.parse_light_client_update(updates.json())
        
    #     process_light_client_update(light_client_store, light_client_update, current_slot, genesis_validators_root)
        
    #     time.sleep(1)