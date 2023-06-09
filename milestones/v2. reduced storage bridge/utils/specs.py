# """
# This is a copy of part of the ethereum specs
# """

from dataclasses import (
    dataclass,
)


from utils.ssz.ssz_typing import (
    uint64, Bytes32, Container, Vector, Bytes48, ByteList, uint256,
    ByteVector, Bytes20, Bytes96, Bitvector
)


from typing import NewType, Optional, NamedTuple


GeneralizedIndex = NewType('GeneralizedIndex', int)


class Slot(uint64):
    pass


class Epoch(uint64):
    pass


class ValidatorIndex(uint64):
    pass


class Root(Bytes32):
    pass


class Hash32(Bytes32):
    pass


class BLSPubkey(Bytes48):
    pass


class BLSSignature(Bytes96):
    pass


class ExecutionAddress(Bytes20):
    pass


def floorlog2(x: int) -> uint64:
    if x < 1:
        raise ValueError(f"floorlog2 accepts only positive values, x={x}")
    return uint64(x.bit_length() - 1)


FINALIZED_ROOT_INDEX = GeneralizedIndex(105)
CURRENT_SYNC_COMMITTEE_INDEX = GeneralizedIndex(54)
NEXT_SYNC_COMMITTEE_INDEX = GeneralizedIndex(55)
EXECUTION_PAYLOAD_INDEX = GeneralizedIndex(25)

# Constant vars
MAX_REQUEST_LIGHT_CLIENT_UPDATES = 2**7

# Preset vars
SLOTS_PER_EPOCH = uint64(32)
SYNC_COMMITTEE_SIZE = uint64(512)
EPOCHS_PER_SYNC_COMMITTEE_PERIOD = uint64(256)
BYTES_PER_LOGS_BLOOM = uint64(256)
MAX_EXTRA_DATA_BYTES = 32

class Configuration(NamedTuple):
    SECONDS_PER_SLOT: uint64
    MIN_GENESIS_TIME: uint64


config = Configuration(
    SECONDS_PER_SLOT=uint64(12),
    MIN_GENESIS_TIME=uint64(1606824000),
)



class BeaconBlockHeader(Container):
    slot: Slot
    proposer_index: ValidatorIndex
    parent_root: Root
    state_root: Root
    body_root: Root


class SyncAggregate(Container):
    sync_committee_bits: Bitvector[SYNC_COMMITTEE_SIZE]
    sync_committee_signature: BLSSignature


class SyncCommittee(Container):
    pubkeys: Vector[BLSPubkey, SYNC_COMMITTEE_SIZE]
    aggregate_pubkey: BLSPubkey


class ExecutionPayloadHeader(Container):
    # Execution block header fields
    parent_hash: Hash32
    fee_recipient: ExecutionAddress
    state_root: Bytes32
    receipts_root: Bytes32
    logs_bloom: ByteVector[BYTES_PER_LOGS_BLOOM]
    prev_randao: Bytes32
    block_number: uint64
    gas_limit: uint64
    gas_used: uint64
    timestamp: uint64
    extra_data: ByteList[MAX_EXTRA_DATA_BYTES]
    base_fee_per_gas: uint256
    # Extra payload fields
    block_hash: Hash32  # Hash of execution block
    transactions_root: Root
    withdrawals_root: Root  # [New in Capella]


class LightClientHeader(Container):
    # Beacon block header
    beacon: BeaconBlockHeader
    # Execution payload header corresponding to `beacon.body_root` (from Capella onward)
    execution: ExecutionPayloadHeader
    execution_branch: Vector[Bytes32, floorlog2(EXECUTION_PAYLOAD_INDEX)]


class LightClientOptimisticUpdate(Container):
    # Header attested to by the sync committee
    attested_header: LightClientHeader
    # Sync committee aggregate signature
    sync_aggregate: SyncAggregate
    # Slot at which the aggregate signature was created (untrusted)
    signature_slot: Slot


class LightClientFinalityUpdate(Container):
    # Header attested to by the sync committee
    attested_header: LightClientHeader
    # Finalized header corresponding to `attested_header.beacon.state_root`
    finalized_header: LightClientHeader
    finality_branch: Vector[Bytes32, floorlog2(FINALIZED_ROOT_INDEX)]
    # Sync committee aggregate signature
    sync_aggregate: SyncAggregate
    # Slot at which the aggregate signature was created (untrusted)
    signature_slot: Slot


class LightClientUpdate(Container):
    # Header attested to by the sync committee
    attested_header: LightClientHeader
    # Next sync committee corresponding to `attested_header.beacon.state_root`
    next_sync_committee: SyncCommittee
    next_sync_committee_branch: Vector[Bytes32,
                                       floorlog2(NEXT_SYNC_COMMITTEE_INDEX)]
    # Finalized header corresponding to `attested_header.beacon.state_root`
    finalized_header: LightClientHeader
    finality_branch: Vector[Bytes32, floorlog2(FINALIZED_ROOT_INDEX)]
    # Sync committee aggregate signature
    sync_aggregate: SyncAggregate
    # Slot at which the aggregate signature was created (untrusted)
    signature_slot: Slot


class LightClientBootstrap(Container):
    # Header matching the requested beacon block root
    header: LightClientHeader
    # Current sync committee corresponding to `header.beacon.state_root`
    current_sync_committee: SyncCommittee
    current_sync_committee_branch: Vector[Bytes32, floorlog2(
        CURRENT_SYNC_COMMITTEE_INDEX)]


@dataclass
class LightClientStore(object):
    # Header that is finalized
    finalized_header: LightClientHeader
    # Sync committees corresponding to the finalized header
    current_sync_committee: SyncCommittee
    next_sync_committee: SyncCommittee
    # Best available header to switch finalized head to if we see nothing else
    best_valid_update: Optional[LightClientUpdate]
    # Most recent available reasonably-safe header
    optimistic_header: LightClientHeader
    # Max number of active participants in a sync committee (used to calculate safety threshold)
    previous_max_active_participants: uint64
    current_max_active_participants: uint64


def compute_epoch_at_slot(slot: Slot) -> Epoch:
    """
    Return the epoch number at ``slot``.
    """
    return Epoch(slot // SLOTS_PER_EPOCH)


def compute_sync_committee_period_at_slot(slot: Slot) -> uint64:
    return compute_sync_committee_period(compute_epoch_at_slot(slot))


def compute_sync_committee_period(epoch: Epoch) -> uint64:
    return epoch // EPOCHS_PER_SYNC_COMMITTEE_PERIOD