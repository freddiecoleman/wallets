import asyncio
from custody_wallet.custody_wallet import CPWallet
from chiasim.clients.ledger_sim import connect_to_ledger_sim
from chiasim.wallet.deltas import additions_for_body, removals_for_body
from chiasim.hashable import Coin
from chiasim.hashable.Body import BodyList
from utilities.decorations import print_leaf, divider, prompt
from chiasim.hashable import ProgramHash
from binascii import hexlify
from chiasim.atoms import hexbytes, uint64


def get_int(message):
    amount = ""
    while amount == "":
        amount = input(message)
        if amount == "q":
            return "q"
        if not amount.isdigit():
            amount = ""
        if amount.isdigit():
            amount = int(amount)
    return amount


async def print_my_details(wallet, ledger_api):
    print()
    print(divider)
    print(" \u2447 Wallet Details \u2447")
    print()
    print("Name: " + wallet.name)
    pk = hexlify(wallet.get_next_public_key().serialize()).decode("ascii")
    print(f"New pubkey: {pk}")
    current_time = await ledger_api.skip_milliseconds(ms=uint64(1000).to_bytes(4, 'big'))
    print(f"Current time in milliseconds is: {current_time}")
    print(divider)


def view_funds(wallet):
    print(f"Regular Balance: {wallet.current_balance}")
    print(f"Current Custody Balance: {wallet.cp_balance}")


async def approve_transaction(wallet):
    print(f"Authorizing pubkey: {wallet.pubkey_approval}")
    print("Enter new that need's a approval:")
    newpubkey = input("Enter pubkey for new custody: ")
    puzzlehash = ProgramHash(wallet.cp_puzzle(newpubkey, wallet.pubkey_approval, wallet.unlock_time))
    amount = get_int("Enter amount: ")
    output = puzzlehash, amount
    outputs = [output]
    solution = wallet.solution_for_cp_permission(outputs)
    approval = wallet.cp_approval_signature_for_transaction(solution).sig
    print(f"Approving signature: {approval}")


async def create_custody(wallet, ledger_api):
    option = input("Press (c) for custodian, or press (a) for authorizer")
    if option == "c":
        print("Custodian selected")
        pubkey = hexbytes(wallet.get_next_public_key().serialize())
        print(f"Custodian pubkey: {pubkey}")
        pubkey_auth = input("Enter Authorizer's pubkey: ")
        wallet.pubkey_permission = pubkey_auth
        unlock_time = get_int("Specify unlock time (milliseconds since 1970): ")
        wallet.unlock_time = unlock_time
        return
    elif option == "a":
        pubkey = hexbytes(wallet.get_next_public_key().serialize())
        wallet.pubkey_approval = pubkey
        print("Authorizer Selected")
        print(f"Authorizer pubkey is: {pubkey}")
        pubkey_custody = input("Enter Custodian's pubkey: ")
        unlock_time = get_int("Enter the same lock time as in custody wallet: ")
        amount = get_int("Enter Chia amount to send to custody: ")
        wallet.unlock_time = unlock_time

        puzzle_hash = ProgramHash(wallet.cp_puzzle(pubkey_custody, pubkey, unlock_time))
        spend_bundle = wallet.generate_signed_transaction(amount, puzzle_hash)
        _ = await ledger_api.push_tx(tx=spend_bundle)
        return
    else:
        print("Invalid option, returning...")
        return


async def move_custody(wallet, ledger_api):
    print("Moving custody")
    print(f"Amount being moved: {wallet.cp_balance}")
    amount = wallet.cp_balance
    unlock_time = wallet.unlock_time
    pubkey_permission = wallet.pubkey_permission
    current_time = await ledger_api.skip_milliseconds(ms=uint64(0).to_bytes(4, 'big'))
    if unlock_time > current_time:
        new_pub = input("Enter pubkey of the new custodian wallet: ")
        print("Permission needed before moving funds: ")
        puzzle_hash = ProgramHash(wallet.cp_puzzle(new_pub, pubkey_permission, unlock_time))
        approval = input("\nAdd authorization: ")
        approval = bytes.fromhex(approval)
        spend_bundle = wallet.cp_generate_signed_transaction_with_approval(puzzle_hash, amount, approval)
        _ = await ledger_api.push_tx(tx=spend_bundle)
    else:
        print("Permission not needed for moving funds")
        pubkey = input("Enter receiver pubkey: ")
        pub_bytes = bytes.fromhex(pubkey)
        reg_puzzle = wallet.puzzle_for_pk(pub_bytes)
        puzzle_hash = ProgramHash(reg_puzzle)
        spend_bundle = wallet.cp_generate_signed_transaction(puzzle_hash, amount)
        _ = await ledger_api.push_tx(tx=spend_bundle)


async def update_ledger(wallet, ledger_api, most_recent_header):
    if most_recent_header is None:
        r = await ledger_api.get_all_blocks()
    else:
        r = await ledger_api.get_recent_blocks(most_recent_header=most_recent_header)
    update_list = BodyList.from_bytes(r)
    tip = await ledger_api.get_tip()
    index = int(tip["tip_index"])
    for body in update_list:
        additions = list(additions_for_body(body))
        removals = removals_for_body(body)
        removals = [Coin.from_bytes(await ledger_api.hash_preimage(hash=x)) for x in removals]
        spend_bundle_list = wallet.notify(additions, removals, index)
        if spend_bundle_list is not None:
            for spend_bundle in spend_bundle_list:
                _ = await ledger_api.push_tx(tx=spend_bundle)

    return most_recent_header


async def new_block(wallet, ledger_api):
    coinbase_puzzle_hash = wallet.get_new_puzzlehash()
    fees_puzzle_hash = wallet.get_new_puzzlehash()
    r = await ledger_api.next_block(coinbase_puzzle_hash=coinbase_puzzle_hash, fees_puzzle_hash=fees_puzzle_hash)
    body = r["body"]
    tip = await  ledger_api.get_tip()
    index = tip["tip_index"]
    most_recent_header = r['header']
    additions = list(additions_for_body(body))
    removals = removals_for_body(body)
    removals = [Coin.from_bytes(await ledger_api.hash_preimage(hash=x)) for x in removals]
    wallet.notify(additions, removals, index)
    return most_recent_header


async def main_loop():
    ledger_api = await connect_to_ledger_sim("localhost", 9868)
    selection = ""
    wallet = CPWallet()
    most_recent_header = None
    print_leaf()
    print()
    print("Welcome to your Chia Custody Wallet.")
    print()
    my_pubkey_orig = wallet.get_next_public_key().serialize()
    wallet.pubkey_approval = hexbytes(wallet.get_next_public_key().serialize())
    wallet.pubkey_orig = my_pubkey_orig
    print("Your pubkey is: " + hexlify(my_pubkey_orig).decode('ascii'))

    while selection != "q":
        print()
        print(divider)
        print(" \u2447 Menu \u2447")
        print()
        tip = await ledger_api.get_tip()
        print("Block: ", tip["tip_index"])
        print()
        print("Select a function:")
        print("\u2448 1 Wallet Details")
        print("\u2448 2 View Funds")
        print("\u2448 3 Get Update")
        print("\u2448 4 *GOD MODE* Farm Block / Get Money")
        print("\u2448 5 Create Custody")
        print("\u2448 6 Move Custody")
        print("\u2448 7 Approve Transaction")
        print("\u2448 q Quit")
        print(divider)
        print()

        selection = input(prompt)
        if selection == "1":
            await print_my_details(wallet, ledger_api)
        elif selection == "2":
            view_funds(wallet)
        elif selection == "3":
            most_recent_header = await update_ledger(wallet, ledger_api, most_recent_header)
        elif selection == "4":
            most_recent_header = await new_block(wallet, ledger_api)
        elif selection == "5":
            await create_custody(wallet, ledger_api)
        elif selection == "6":
            await move_custody(wallet, ledger_api)
        elif selection == "7":
            await approve_transaction(wallet)


def main():
    run = asyncio.get_event_loop().run_until_complete
    run(main_loop())


if __name__ == "__main__":
    main()


"""
Copyright 2018 Chia Network Inc
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
   http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""