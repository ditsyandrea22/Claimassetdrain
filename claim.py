#!/usr/bin/env python3
import time
import os
import json
from web3 import Web3, exceptions
from web3.gas_strategies.time_based import fast_gas_price_strategy
from dotenv import load_dotenv
import questionary
from termcolor import colored
from datetime import datetime
import requests

# Load environment variables
load_dotenv()

# ===== CONFIGURATION =====
class Config:
    # Ethereum Mainnet RPC (can be replaced with your preferred provider)
    RPC_URL = os.getenv("RPC_URL", "https://eth.llamarpc.com")
    
    # Gas settings
    MAX_GAS_GWEI = float(os.getenv("MAX_GAS_GWEI", 50))  # Default max gas of 50 Gwei
    GAS_PRIORITY_FEE = float(os.getenv("GAS_PRIORITY_FEE", 1.5))  # Priority fee in Gwei
    BASE_FEE_MULTIPLIER = float(os.getenv("BASE_FEE_MULTIPLIER", 1.3))  # Multiplier for base fee
    
    # Transaction settings
    DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
    CONFIRMATION_BLOCKS = int(os.getenv("CONFIRMATION_BLOCKS", 3))
    MAX_RETRIES = 3  # Max retries for failed transactions
    RETRY_DELAY = 15  # Seconds between retries
    
    # FastBot settings
    FASTBOT_ENABLED = os.getenv("FASTBOT_ENABLED", "true").lower() == "true"
    FASTBOT_GAS_MULTIPLIER = float(os.getenv("FASTBOT_GAS_MULTIPLIER", 1.5))
    
    # Timing settings
    TRANSFER_DELAY = int(os.getenv("TRANSFER_DELAY", 5))  # Seconds between transfers
    AIRDROP_CHECK_INTERVAL = 300  # 5 minutes between airdrop checks
    GAS_WAIT_TIMEOUT = 600  # 10 minutes max wait for optimal gas
    GAS_WAIT_THRESHOLD = 0.8  # 80% of max gas we're willing to pay
    CONFIRMATION_TIMEOUT = 300  # 5 minutes max wait for confirmations
    
    # Security settings
    MIN_ETH_BALANCE = 0.001  # Minimum ETH balance required (0.001 ETH)
    GAS_LIMIT_BUFFER = 1.3  # 30% buffer on estimated gas limit

# ===== INITIALIZE WEB3 =====
w3 = Web3(Web3.HTTPProvider(Config.RPC_URL))
w3.eth.set_gas_price_strategy(fast_gas_price_strategy)

# ===== ABI =====
ERC20_ABI = json.loads('''[
    {"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]''')

# ===== UTILITIES =====
def get_current_gas():
    """Get current gas price in Gwei with EIP-1559 support"""
    try:
        # Get the latest block to access base fee
        latest_block = w3.eth.get_block('latest')
        base_fee = latest_block['baseFeePerGas'] / 1e9  # Convert to Gwei
        
        # Calculate max fee per gas (base fee * multiplier + priority fee)
        max_fee_per_gas = (base_fee * Config.BASE_FEE_MULTIPLIER) + Config.GAS_PRIORITY_FEE
        
        # Don't exceed our max gas price
        max_fee_per_gas = min(max_fee_per_gas, Config.MAX_GAS_GWEI)
        
        return {
            'base_fee': base_fee,
            'max_fee_per_gas': max_fee_per_gas,
            'priority_fee': Config.GAS_PRIORITY_FEE
        }
    except Exception as e:
        print(colored(f"⚠️ Gas price check failed: {str(e)}", 'yellow'))
        # Fallback to legacy gas price
        try:
            gas_price = w3.eth.gas_price / 1e9  # Convert to Gwei
            return {
                'max_fee_per_gas': min(gas_price, Config.MAX_GAS_GWEI),
                'priority_fee': Config.GAS_PRIORITY_FEE,
                'legacy': True
            }
        except:
            # Ultimate fallback
            return {
                'max_fee_per_gas': min(50, Config.MAX_GAS_GWEI),
                'priority_fee': Config.GAS_PRIORITY_FEE,
                'legacy': True
            }

def wait_for_optimal_gas(max_gas):
    """Wait until gas price drops below our threshold"""
    start_time = time.time()
    threshold = max_gas * Config.GAS_WAIT_THRESHOLD
    print(colored(f"⏳ Waiting for gas ≤ {threshold:.2f} Gwei (current max: {max_gas:.2f})...", 'yellow'))
    
    while True:
        gas_info = get_current_gas()
        current_gas = gas_info['max_fee_per_gas']
        
        if current_gas <= threshold:
            print(colored(f"✅ Optimal gas reached: {current_gas:.2f} Gwei", 'green'))
            return gas_info
        
        if time.time() - start_time > Config.GAS_WAIT_TIMEOUT:
            print(colored(f"⚠️ Gas wait timeout reached, using current gas: {current_gas:.2f} Gwei", 'yellow'))
            return gas_info
        
        # Show countdown
        remaining = int(Config.GAS_WAIT_TIMEOUT - (time.time() - start_time))
        print(colored(f"   Current gas: {current_gas:.2f} Gwei | Waiting... {remaining}s remaining", 'blue'))
        time.sleep(15)

def wait_for_transaction(tx_hash):
    """Wait for transaction confirmation with enhanced monitoring"""
    start_time = time.time()
    last_block = w3.eth.block_number
    
    print(colored("⏳ Waiting for transaction confirmation...", 'yellow'))
    
    while True:
        try:
            # Check if transaction has been mined
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None:
                if receipt.status == 1:
                    print(colored(f"✅ Transaction confirmed in block {receipt.blockNumber}", 'green'))
                    print(colored(f"📊 Gas used: {receipt.gasUsed}", 'blue'))
                    return True, receipt
                else:
                    print(colored("❌ Transaction failed in block", 'red'))
                    return False, receipt

            # Check if stuck (no new blocks)
            current_block = w3.eth.block_number
            if current_block == last_block:
                if time.time() - start_time > Config.CONFIRMATION_TIMEOUT:
                    print(colored("⚠️ Transaction seems stuck - no new blocks", 'red'))
                    return False, None
            else:
                last_block = current_block

            # Check for timeout
            if time.time() - start_time > Config.CONFIRMATION_TIMEOUT:
                print(colored("⚠️ Confirmation timeout reached", 'red'))
                return False, None

            # Show progress
            elapsed = int(time.time() - start_time)
            print(colored(f"   Current block: {current_block} | Waiting... {elapsed}s elapsed", 'blue'))
            time.sleep(5)

        except exceptions.TransactionNotFound:
            # Transaction not found in mempool yet
            if time.time() - start_time > 120:  # 2 minutes
                print(colored("⚠️ Transaction not found in mempool", 'red'))
                return False, None
            time.sleep(5)
        except Exception as e:
            print(colored(f"⚠️ Confirmation error: {str(e)}", 'yellow'))
            time.sleep(5)

def load_wallets():
    """Load wallets from JSON file with validation"""
    try:
        with open('wallets.json') as f:
            wallets = json.load(f)
            
            # Validate wallet format
            valid_wallets = []
            for wallet in wallets:
                if 'address' in wallet and 'private_key' in wallet:
                    if Web3.is_address(wallet['address']):
                        # Ensure private key has 0x prefix
                        priv_key = wallet['private_key']
                        if not priv_key.startswith('0x'):
                            priv_key = '0x' + priv_key
                        valid_wallets.append({
                            'address': Web3.to_checksum_address(wallet['address']),
                            'private_key': priv_key
                        })
                    else:
                        print(colored(f"⚠️ Invalid address in wallet: {wallet['address']}", 'yellow'))
                else:
                    print(colored("⚠️ Invalid wallet format (missing address or private_key)", 'yellow'))
            
            if not valid_wallets:
                print(colored("❌ No valid wallets found in wallets.json", 'red'))
            
            return valid_wallets
    except FileNotFoundError:
        print(colored("❌ wallets.json file not found!", 'red'))
        return []
    except json.JSONDecodeError:
        print(colored("❌ Invalid JSON format in wallets.json", 'red'))
        return []

def save_failed_wallet(wallet_address, reason, tx_hash=None):
    """Save failed wallets to a file with additional details"""
    os.makedirs('logs', exist_ok=True)
    with open('logs/failed_wallets.json', 'a') as f:
        data = {
            'address': wallet_address,
            'reason': reason,
            'tx_hash': tx_hash.hex() if tx_hash else None,
            'timestamp': datetime.now().isoformat(),
            'rpc_url': Config.RPC_URL
        }
        f.write(json.dumps(data) + '\n')

def check_eth_balance(address):
    """Check ETH balance with retries"""
    for attempt in range(3):
        try:
            balance = w3.eth.get_balance(address)
            return balance / 1e18  # Convert from Wei to ETH
        except Exception as e:
            if attempt == 2:
                print(colored(f"⚠️ Balance check failed: {str(e)}", 'yellow'))
                return 0
            time.sleep(2)

def estimate_transfer_gas(token_contract, from_address, to_address, amount):
    """Estimate gas for token transfer with multiple fallbacks"""
    try:
        # First try with standard estimation
        gas = token_contract.functions.transfer(
            to_address,
            amount
        ).estimate_gas({'from': from_address})
        
        # Add safety margin
        return int(gas * Config.GAS_LIMIT_BUFFER)
    except Exception as e:
        print(colored(f"⚠️ Gas estimation failed: {str(e)}", 'yellow'))
        return 200000  # Default gas limit for ERC20 transfers

def check_airdrop_eligibility(wallet_address, token_contract):
    """Enhanced airdrop eligibility check with retries"""
    for attempt in range(3):
        try:
            balance = token_contract.functions.balanceOf(wallet_address).call()
            eth_balance = check_eth_balance(wallet_address)
            decimals = token_contract.functions.decimals().call()
            
            return {
                'has_tokens': balance > 0,
                'token_balance': balance,
                'human_balance': balance / (10 ** decimals),
                'has_gas': eth_balance >= Config.MIN_ETH_BALANCE,
                'eth_balance': eth_balance
            }
        except Exception as e:
            if attempt == 2:
                print(colored(f"⚠️ Airdrop check error: {str(e)}", 'yellow'))
                return None
            time.sleep(2)

def fastbot_transfer(wallet_address, private_key, token_contract, safe_address):
    """Ultra-fast token transfer with boosted gas and enhanced features"""
    try:
        # Get token info first
        balance = token_contract.functions.balanceOf(wallet_address).call()
        if balance == 0:
            return False
        
        decimals = token_contract.functions.decimals().call()
        human_balance = balance / (10 ** decimals)
        print(colored(f"   💰 Balance: {human_balance:.6f}", 'green'))
        
        # Check ETH balance
        eth_balance = check_eth_balance(wallet_address)
        if eth_balance < Config.MIN_ETH_BALANCE:
            print(colored(f"   ❌ Insufficient ETH for gas ({eth_balance:.6f} ETH)", 'red'))
            save_failed_wallet(wallet_address, "Insufficient ETH")
            return False
        
        # Get boosted gas price
        gas_info = get_current_gas()
        boosted_max_fee = min(gas_info['max_fee_per_gas'] * Config.FASTBOT_GAS_MULTIPLIER, Config.MAX_GAS_GWEI)
        boosted_priority_fee = min(gas_info['priority_fee'] * Config.FASTBOT_GAS_MULTIPLIER, Config.MAX_GAS_GWEI * 0.5)
        
        print(colored(f"   ⛽ Boosted Gas: {boosted_max_fee:.2f} Gwei (Priority: {boosted_priority_fee:.2f})", 'magenta'))
        
        # Estimate gas with safety margin
        gas_limit = estimate_transfer_gas(token_contract, wallet_address, safe_address, balance)
        
        # Build transaction
        nonce = w3.eth.get_transaction_count(wallet_address)
        
        tx_params = {
            'chainId': w3.eth.chain_id,
            'gas': gas_limit,
            'nonce': nonce,
        }
        
        # Use EIP-1559 if possible, otherwise fallback to legacy
        if 'legacy' not in gas_info or not gas_info['legacy']:
            tx_params['maxFeePerGas'] = w3.to_wei(boosted_max_fee, 'gwei')
            tx_params['maxPriorityFeePerGas'] = w3.to_wei(boosted_priority_fee, 'gwei')
        else:
            tx_params['gasPrice'] = w3.to_wei(boosted_max_fee, 'gwei')
        
        tx = token_contract.functions.transfer(
            safe_address,
            balance
        ).build_transaction(tx_params)
        
        # Calculate total cost
        if 'maxFeePerGas' in tx:
            total_cost = tx['gas'] * tx['maxFeePerGas']
        else:
            total_cost = tx['gas'] * tx['gasPrice']
            
        print(colored(f"   💸 Estimated cost: {w3.from_wei(total_cost, 'ether'):.6f} ETH", 'blue'))
        
        if Config.DRY_RUN:
            print(colored("   🚧 Dry run - skipping actual transfer", 'yellow'))
            return True

        # Sign and send
        signed_tx = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        print(colored(f"   🔗 Tx Hash: {tx_hash.hex()}", 'magenta'))
        
        # Quick check if transaction is in mempool
        time.sleep(2)  # Give node some time to process
        try:
            if w3.eth.get_transaction(tx_hash):
                print(colored("   ✔️ Transaction successfully broadcast", 'green'))
                return True
        except:
            print(colored("   ⚠️ Transaction not yet in mempool", 'yellow'))
        
        return True
    except ValueError as e:
        if 'nonce too low' in str(e):
            print(colored("   ⚠️ Nonce too low, retrying with new nonce", 'yellow'))
            return fastbot_transfer(wallet_address, private_key, token_contract, safe_address)
        print(colored(f"   ❌ FastBot error: {str(e)}", 'red'))
        save_failed_wallet(wallet_address, str(e))
        return False
    except Exception as e:
        print(colored(f"   ❌ FastBot error: {str(e)}", 'red'))
        save_failed_wallet(wallet_address, str(e))
        return False

# ===== CORE FUNCTIONS =====
def transfer_tokens(wallet_address, private_key, token_contract, safe_address):
    """Secure token transfer with enhanced gas optimization and confirmation waiting"""
    for attempt in range(Config.MAX_RETRIES):
        try:
            # Check token balance
            balance = token_contract.functions.balanceOf(wallet_address).call()
            if balance == 0:
                print(colored("   ⚠️ No tokens to transfer", 'yellow'))
                return False

            decimals = token_contract.functions.decimals().call()
            human_balance = balance / (10 ** decimals)
            print(colored(f"   💰 Balance: {human_balance:.6f}", 'green'))

            # Check ETH balance
            eth_balance = check_eth_balance(wallet_address)
            if eth_balance < Config.MIN_ETH_BALANCE:
                print(colored(f"   ❌ Insufficient ETH for gas ({eth_balance:.6f} ETH)", 'red'))
                save_failed_wallet(wallet_address, "Insufficient ETH")
                return False

            # Get optimal gas price
            gas_info = get_current_gas()
            if gas_info['max_fee_per_gas'] > Config.MAX_GAS_GWEI * Config.GAS_WAIT_THRESHOLD:
                print(colored(f"   ⛽ Current Gas: {gas_info['max_fee_per_gas']:.2f} Gwei (Above threshold)", 'yellow'))
                gas_info = wait_for_optimal_gas(Config.MAX_GAS_GWEI)
            else:
                print(colored(f"   ⛽ Current Gas: {gas_info['max_fee_per_gas']:.2f} Gwei", 'blue'))

            # Estimate gas with safety margin
            gas_limit = estimate_transfer_gas(token_contract, wallet_address, safe_address, balance)
            print(colored(f"   ⚡ Gas Limit: {gas_limit}", 'blue'))

            if Config.DRY_RUN:
                print(colored("   🚧 Dry run - skipping actual transfer", 'yellow'))
                return True

            # Build transaction
            nonce = w3.eth.get_transaction_count(wallet_address)
            
            tx_params = {
                'chainId': w3.eth.chain_id,
                'gas': gas_limit,
                'nonce': nonce,
            }
            
            # Use EIP-1559 if possible, otherwise fallback to legacy
            if 'legacy' not in gas_info or not gas_info['legacy']:
                tx_params['maxFeePerGas'] = w3.to_wei(gas_info['max_fee_per_gas'], 'gwei')
                tx_params['maxPriorityFeePerGas'] = w3.to_wei(gas_info['priority_fee'], 'gwei')
            else:
                tx_params['gasPrice'] = w3.to_wei(gas_info['max_fee_per_gas'], 'gwei')
            
            tx = token_contract.functions.transfer(
                safe_address,
                balance
            ).build_transaction(tx_params)

            # Calculate total cost
            if 'maxFeePerGas' in tx_params:
                total_cost = gas_limit * tx_params['maxFeePerGas']
            else:
                total_cost = gas_limit * tx_params['gasPrice']
                
            print(colored(f"   💸 Estimated cost: {w3.from_wei(total_cost, 'ether'):.6f} ETH", 'blue'))

            # Sign and send
            signed_tx = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            print(colored(f"   🔗 Tx Hash: {tx_hash.hex()}", 'magenta'))

            # Wait for confirmation
            success, receipt = wait_for_transaction(tx_hash)
            if success:
                print(colored("   ✅ Transfer successful!", 'green'))
                if receipt:
                    print(colored(f"   📊 Gas Used: {receipt.gasUsed} ({(receipt.gasUsed/gas_limit)*100:.1f}% of limit)", 'blue'))
                return True
            else:
                print(colored("   ❌ Transfer failed!", 'red'))
                if attempt < Config.MAX_RETRIES - 1:
                    print(colored(f"   ⏳ Retrying in {Config.RETRY_DELAY}s... ({attempt + 2}/{Config.MAX_RETRIES})", 'yellow'))
                    time.sleep(Config.RETRY_DELAY)
                    continue
                save_failed_wallet(wallet_address, "Transfer failed after retries", tx_hash)
                return False

        except ValueError as e:
            if 'nonce too low' in str(e):
                print(colored("   ⚠️ Nonce too low, retrying with new nonce", 'yellow'))
                continue
            print(colored(f"   ❌ Error: {str(e)}", 'red'))
            if attempt < Config.MAX_RETRIES - 1:
                print(colored(f"   ⏳ Retrying in {Config.RETRY_DELAY}s... ({attempt + 2}/{Config.MAX_RETRIES})", 'yellow'))
                time.sleep(Config.RETRY_DELAY)
                continue
            save_failed_wallet(wallet_address, str(e))
            return False
        except Exception as e:
            print(colored(f"   ❌ Error: {str(e)}", 'red'))
            if attempt < Config.MAX_RETRIES - 1:
                print(colored(f"   ⏳ Retrying in {Config.RETRY_DELAY}s... ({attempt + 2}/{Config.MAX_RETRIES})", 'yellow'))
                time.sleep(Config.RETRY_DELAY)
                continue
            save_failed_wallet(wallet_address, str(e))
            return False

def monitor_airdrops(wallets, token_contract, safe_address):
    """Continuous monitoring for new tokens with enhanced features"""
    print(colored("\n🔍 Starting monitoring service...", 'blue', attrs=['bold']))
    print(colored(f"ℹ️ Checking every {Config.AIRDROP_CHECK_INTERVAL/60:.1f} minutes", 'blue'))
    
    # Get token info for display
    try:
        symbol = token_contract.functions.symbol().call()
        name = token_contract.functions.name().call()
        decimals = token_contract.functions.decimals().call()
        total_supply = token_contract.functions.totalSupply().call() / (10 ** decimals)
        print(colored(f"Token: {name} ({symbol}) | Total Supply: {total_supply:,.2f}", 'cyan'))
    except:
        print(colored("Token: (Unknown)", 'cyan'))
    
    while True:
        try:
            current_block = w3.eth.block_number
            print(colored(f"\n🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Block: {current_block}", 'cyan'))
            
            for wallet in wallets:
                wallet_address = wallet['address']
                short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
                print(colored(f"\nChecking {short_address}", 'cyan'))
                
                status = check_airdrop_eligibility(wallet_address, token_contract)
                if not status:
                    time.sleep(2)  # Brief pause between checks
                    continue
                
                if status['has_tokens']:
                    print(colored(f"   🎁 Tokens found: {status['human_balance']:.6f} {symbol}", 'green'))
                    print(colored(f"   ⛽ ETH balance: {status['eth_balance']:.6f}", 'blue'))
                    
                    if Config.FASTBOT_ENABLED:
                        print(colored("   ⚡ FastBot transfer initiated...", 'magenta'))
                        if not fastbot_transfer(wallet_address, wallet['private_key'], token_contract, safe_address):
                            print(colored("   ⚠️ Falling back to normal transfer", 'yellow'))
                            transfer_tokens(wallet_address, wallet['private_key'], token_contract, safe_address)
                    else:
                        transfer_tokens(wallet_address, wallet['private_key'], token_contract, safe_address)
                else:
                    print(colored("   ⚠️ No tokens available", 'yellow'))
                
                time.sleep(Config.TRANSFER_DELAY)
            
            # Show countdown until next check
            for remaining in range(Config.AIRDROP_CHECK_INTERVAL, 0, -60):
                print(colored(f"\nNext check in {remaining//60} minutes...", 'blue'))
                time.sleep(min(60, remaining))
            
        except KeyboardInterrupt:
            print(colored("\n🛑 Monitoring stopped by user", 'red'))
            break
        except Exception as e:
            print(colored(f"⚠️ Monitoring error: {str(e)}", 'yellow'))
            time.sleep(60)  # Wait a minute before retrying after error

# ===== MAIN FLOW =====
def main():
    print(colored("\n🔥 ETH L1 Token Claim Bot v4.2 🔥", 'red', attrs=['bold']))
    print(colored("🚀 Ultra-Fast Transfers | EIP-1559 Support | Enhanced Security\n", 'yellow'))
    print(colored(f"ℹ️ Connected to: {Config.RPC_URL}", 'blue'))
    print(colored(f"ℹ️ Chain ID: {w3.eth.chain_id} | Network: {'Mainnet' if w3.eth.chain_id == 1 else 'Testnet' if w3.eth.chain_id == 5 else 'Unknown'}", 'blue'))
    print(colored(f"ℹ️ Max gas price: {Config.MAX_GAS_GWEI} Gwei | Priority fee: {Config.GAS_PRIORITY_FEE} Gwei", 'blue'))
    print(colored(f"ℹ️ FastBot {'enabled' if Config.FASTBOT_ENABLED else 'disabled'} (Multiplier: {Config.FASTBOT_GAS_MULTIPLIER}x)", 'blue'))
    
    # Load wallets
    wallets = load_wallets()
    if not wallets:
        return

    # Get token contract
    token_address = questionary.text(
        "Enter token contract address:",
        default="0x..."
    ).ask().strip()

    if not Web3.is_address(token_address):
        print(colored("❌ Invalid token address", 'red'))
        return

    safe_address = questionary.text(
        "Enter safe wallet address:",
        default="0x..."
    ).ask().strip()

    if not Web3.is_address(safe_address):
        print(colored("❌ Invalid safe address", 'red'))
        return

    # Initialize contract
    token_contract = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_ABI
    )

    # Get token info
    try:
        symbol = token_contract.functions.symbol().call()
        name = token_contract.functions.name().call()
        decimals = token_contract.functions.decimals().call()
        total_supply = token_contract.functions.totalSupply().call() / (10 ** decimals)
        print(colored(f"\nToken: {name} ({symbol})", 'cyan'))
        print(colored(f"Decimals: {decimals} | Total Supply: {total_supply:,.2f}", 'cyan'))
    except Exception as e:
        print(colored(f"⚠️ Couldn't get full token info: {str(e)}", 'yellow'))
        symbol = "UNKNOWN"

    # Select mode
    mode = questionary.select(
        "Select mode:",
        choices=[
            "Single Run - Transfer once",
            "Monitoring - Continuous checking",
            "FastBot Only - Quick transfers",
            "Check Balances Only"
        ]
    ).ask()

    successful = 0
    failed = 0
    start_time = time.time()

    if mode == "Single Run - Transfer once":
        for i, wallet in enumerate(wallets):
            wallet_address = wallet['address']
            print(colored(f"\n[{i+1}/{len(wallets)}] {wallet_address[:6]}...{wallet_address[-4:]}", 'cyan', attrs=['bold']))
            
            if transfer_tokens(wallet_address, wallet['private_key'], token_contract, safe_address):
                successful += 1
            else:
                failed += 1

    elif mode == "Monitoring - Continuous checking":
        monitor_airdrops(wallets, token_contract, safe_address)
        return
        
    elif mode == "FastBot Only - Quick transfers":
        for i, wallet in enumerate(wallets):
            wallet_address = wallet['address']
            print(colored(f"\n[{i+1}/{len(wallets)}] FastBot {wallet_address[:6]}...{wallet_address[-4:]}", 'cyan', attrs=['bold']))
            
            if Config.FASTBOT_ENABLED:
                if fastbot_transfer(wallet_address, wallet['private_key'], token_contract, safe_address):
                    successful += 1
                else:
                    failed += 1
            else:
                print(colored("   ⚠️ FastBot disabled in config", 'yellow'))
                if transfer_tokens(wallet_address, wallet['private_key'], token_contract, safe_address):
                    successful += 1
                else:
                    failed += 1
            
            time.sleep(Config.TRANSFER_DELAY)
    
    elif mode == "Check Balances Only":
        print(colored("\n🔍 Checking wallet balances...", 'blue'))
        total_eth = 0
        total_tokens = 0
        
        for wallet in wallets:
            wallet_address = wallet['address']
            status = check_airdrop_eligibility(wallet_address, token_contract)
            
            if status:
                print(colored(f"\n{wallet_address[:6]}...{wallet_address[-4:]}", 'cyan'))
                print(colored(f"   ETH: {status['eth_balance']:.6f}", 'blue'))
                print(colored(f"   {symbol}: {status['human_balance']:.6f}", 'green'))
                
                total_eth += status['eth_balance']
                total_tokens += status['human_balance']
        
        print(colored(f"\n📊 Totals across all wallets:", 'yellow', attrs=['bold']))
        print(colored(f"   Total ETH: {total_eth:.6f}", 'blue'))
        print(colored(f"   Total {symbol}: {total_tokens:.6f}", 'green'))
        return

    # Print summary
    elapsed = time.time() - start_time
    print(colored(f"\n🎉 Complete! Time: {elapsed:.2f}s", 'green', attrs=['bold']))
    print(colored(f"✅ Success: {successful} | ❌ Failed: {failed}", 'green' if not failed else 'yellow'))
    
    if failed:
        print(colored("\n⚠️ Check logs/failed_wallets.json for details", 'yellow'))
    
    # Show final gas price
    gas_info = get_current_gas()
    print(colored(f"\n⛽ Final gas price: {gas_info['max_fee_per_gas']:.2f} Gwei (Priority: {gas_info['priority_fee']:.2f})", 'blue'))

if __name__ == "__main__":
    try:
        # Verify connection
        if not w3.is_connected():
            print(colored("❌ Could not connect to Ethereum node!", 'red'))
            exit(1)
            
        main()
    except KeyboardInterrupt:
        print(colored("\n🛑 Script stopped by user", 'red'))
    except Exception as e:
        print(colored(f"\n❌ Critical error: {str(e)}", 'red'))