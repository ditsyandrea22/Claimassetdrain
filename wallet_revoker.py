import json
import time
import requests
from web3 import Web3
from web3.middleware import geth_poa_middleware
from eth_account import Account
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Any
import warnings

# Suppress warnings
warnings.filterwarnings("ignore")

# Configuration
REVOKE_CASH_API = "https://api.revoke.cash"
INFURA_API_KEY = "YOUR_INFURA_API_KEY"
ETHERSCAN_API_KEY = "YOUR_ETHERSCAN_KEY"
MAX_WORKERS = 20  # Increased thread count for faster processing
GAS_PRICE_MULTIPLIER = 1.3  # Conservative multiplier for reliability
MAX_GAS_PRICE_GWEI = 150  # Max gas price (follows revoke.cash standards)
GAS_LIMIT = 100000  # Gas limit for approve transactions
GAS_SPONSOR_AMOUNT = Web3.to_wei(0.01, 'ether')  # Standard sponsorship amount

# ERC20 ABI snippets
ERC20_ABI = json.loads('[{"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},'
                      '{"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],'
                      '"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},'
                      '{"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],'
                      '"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},'
                      '{"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"}]')

# Supported chains (aligned with revoke.cash)
SUPPORTED_CHAINS = {
    1: {
        'name': 'Ethereum',
        'rpc': f'https://mainnet.infura.io/v3/{INFURA_API_KEY}',
        'explorer': 'https://api.etherscan.io/api',
        'native_token': 'ETH',
        'tx_url': 'https://etherscan.io/tx/'
    },
    56: {
        'name': 'Binance Smart Chain',
        'rpc': 'https://bsc-dataseed.binance.org/',
        'explorer': 'https://api.bscscan.com/api',
        'native_token': 'BNB',
        'tx_url': 'https://bscscan.com/tx/'
    },
    137: {
        'name': 'Polygon',
        'rpc': 'https://polygon-rpc.com/',
        'explorer': 'https://api.polygonscan.com/api',
        'native_token': 'MATIC',
        'tx_url': 'https://polygonscan.com/tx/'
    },
    42161: {
        'name': 'Arbitrum',
        'rpc': 'https://arb1.arbitrum.io/rpc',
        'explorer': 'https://api.arbiscan.io/api',
        'native_token': 'ETH',
        'tx_url': 'https://arbiscan.io/tx/'
    },
    10: {
        'name': 'Optimism',
        'rpc': 'https://mainnet.optimism.io',
        'explorer': 'https://api-optimistic.etherscan.io/api',
        'native_token': 'ETH',
        'tx_url': 'https://optimistic.etherscan.io/tx/'
    },
    43114: {
        'name': 'Avalanche',
        'rpc': 'https://api.avax.network/ext/bc/C/rpc',
        'explorer': 'https://api.snowtrace.io/api',
        'native_token': 'AVAX',
        'tx_url': 'https://snowtrace.io/tx/'
    }
}

class RevokeCashAPI:
    @staticmethod
    def get_allowances(wallet_address: str) -> Dict[int, List[Dict]]:
        """Get all allowances using revoke.cash API format"""
        try:
            response = requests.get(
                f"{REVOKE_CASH_API}/allowances",
                params={
                    'address': wallet_address,
                    'chainIds': ','.join(map(str, SUPPORTED_CHAINS.keys()))
                },
                timeout=30
            )
            if response.status_code == 200:
                return response.json()
            print(f"Revoke.cash API error: {response.text}")
        except Exception as e:
            print(f"Error fetching data from revoke.cash: {e}")
        return {}

class ChainScanner:
    @staticmethod
    def get_approvals_for_chain(wallet_address: str, chain_id: int) -> List[Dict]:
        """Fallback to chain explorers if revoke.cash fails"""
        if chain_id not in SUPPORTED_CHAINS:
            return []
        
        chain_data = SUPPORTED_CHAINS[chain_id]
        params = {
            'module': 'account',
            'action': 'tokenapprovalallevents',
            'address': wallet_address,
            'sort': 'desc',
            'apikey': ETHERSCAN_API_KEY
        }
        
        try:
            response = requests.get(chain_data['explorer'], params=params, timeout=20)
            data = response.json()
            if data.get('status') == '1':
                return data.get('result', [])
            print(f"API error on {chain_data['name']}: {data.get('message', 'Unknown error')}")
        except Exception as e:
            print(f"Error fetching approvals on {chain_data['name']}: {e}")
        return []

class GasSponsor:
    def __init__(self, sponsor_private_key: str):
        self.sponsor_account = Account.from_key(sponsor_private_key)
        self.sponsor_address = self.sponsor_account.address
        self.chain_clients = {}
        
    def setup_chain_client(self, chain_id: int) -> Optional[Web3]:
        """Setup Web3 client for a specific chain"""
        if chain_id not in SUPPORTED_CHAINS:
            return None
            
        if chain_id in self.chain_clients:
            return self.chain_clients[chain_id]
            
        chain_data = SUPPORTED_CHAINS[chain_id]
        w3 = Web3(Web3.HTTPProvider(chain_data['rpc']))
        
        if not w3.is_connected():
            print(f"Failed to connect to {chain_data['name']}")
            return None
            
        # Inject POA middleware if needed
        if chain_id in [56, 137, 43114]:  # BSC, Polygon, Avalanche are POA
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            
        self.chain_clients[chain_id] = w3
        return w3
        
    def send_gas(self, recipient: str, chain_id: int) -> Optional[str]:
        """Send gas funds following revoke.cash standards"""
        w3 = self.setup_chain_client(chain_id)
        if not w3:
            return None
            
        chain_data = SUPPORTED_CHAINS[chain_id]
        
        try:
            # Check sponsor balance
            balance = w3.eth.get_balance(self.sponsor_address)
            if balance < GAS_SPONSOR_AMOUNT:
                print(f"Insufficient sponsor balance on {chain_data['name']}")
                return None
                
            # Prepare transaction
            tx = {
                'to': recipient,
                'value': GAS_SPONSOR_AMOUNT,
                'gas': 21000,
                'gasPrice': int(w3.eth.gas_price * 1.1),  # Slightly higher gas price
                'nonce': w3.eth.get_transaction_count(self.sponsor_address),
                'chainId': chain_id
            }
            
            # Estimate gas (adjust if needed)
            try:
                tx['gas'] = w3.eth.estimate_gas(tx)
            except:
                pass
                
            signed_tx = w3.eth.account.sign_transaction(tx, self.sponsor_account.key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            # Wait for transaction to be mined
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                return tx_hash.hex()
            return None
            
        except Exception as e:
            print(f"Error sending gas on {chain_data['name']}: {e}")
            return None

class WalletRevoker:
    def __init__(self, target_private_key: str, sponsor_private_key: Optional[str] = None):
        self.target_account = Account.from_key(target_private_key)
        self.target_address = self.target_account.address
        self.chain_clients = {}
        self.gas_sponsor = GasSponsor(sponsor_private_key) if sponsor_private_key else None
        
    def setup_chain_client(self, chain_id: int) -> Optional[Web3]:
        """Setup Web3 client for a specific chain"""
        if chain_id not in SUPPORTED_CHAINS:
            return None
            
        if chain_id in self.chain_clients:
            return self.chain_clients[chain_id]
            
        chain_data = SUPPORTED_CHAINS[chain_id]
        w3 = Web3(Web3.HTTPProvider(chain_data['rpc']))
        
        if not w3.is_connected():
            print(f"Failed to connect to {chain_data['name']}")
            return None
            
        # Inject POA middleware if needed
        if chain_id in [56, 137, 43114]:  # BSC, Polygon, Avalanche are POA
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            
        self.chain_clients[chain_id] = w3
        return w3
        
    def get_token_metadata(self, w3: Web3, token_address: str) -> Dict[str, str]:
        """Get token name and symbol"""
        contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        try:
            name = contract.functions.name().call()
            symbol = contract.functions.symbol().call()
            return {'name': name, 'symbol': symbol}
        except:
            return {'name': 'Unknown', 'symbol': 'UNKNOWN'}
        
    def get_all_allowances(self) -> Dict[int, List[Dict]]:
        """Get all allowances using revoke.cash API with fallback"""
        print("Fetching allowances from revoke.cash API...")
        allowances = RevokeCashAPI.get_allowances(self.target_address)
        
        if not allowances:
            print("Falling back to chain explorers...")
            allowances = {}
            with ThreadPoolExecutor(max_workers=len(SUPPORTED_CHAINS)) as executor:
                future_to_chain = {
                    executor.submit(ChainScanner.get_approvals_for_chain, self.target_address, chain_id): chain_id
                    for chain_id in SUPPORTED_CHAINS
                }
                
                for future in as_completed(future_to_chain):
                    chain_id = future_to_chain[future]
                    try:
                        approvals = future.result()
                        if approvals:
                            allowances[chain_id] = approvals
                    except Exception as e:
                        print(f"Error getting approvals for chain {chain_id}: {e}")
        
        return allowances
        
    def process_allowances(self, allowances: Dict[int, List[Dict]]) -> Dict[int, List[Dict]]:
        """Process raw allowances data into standardized format"""
        processed = {}
        
        for chain_id, chain_allowances in allowances.items():
            w3 = self.setup_chain_client(chain_id)
            if not w3:
                continue
                
            processed_chain = []
            for allowance in chain_allowances:
                try:
                    # Standardize data structure
                    if 'spender' in allowance:  # revoke.cash format
                        item = {
                            'token_address': allowance['token'],
                            'spender': allowance['spender'],
                            'allowance': int(allowance['amount']),
                            'token_info': allowance.get('tokenInfo', {})
                        }
                    else:  # explorer API format
                        metadata = self.get_token_metadata(w3, allowance['contractAddress'])
                        item = {
                            'token_address': allowance['contractAddress'],
                            'spender': allowance['to'],
                            'allowance': int(allowance['value']),
                            'token_info': {
                                'name': metadata['name'],
                                'symbol': metadata['symbol'],
                                'decimals': 18  # Default, can be improved
                            }
                        }
                    
                    # Verify allowance is still active
                    contract = w3.eth.contract(address=item['token_address'], abi=ERC20_ABI)
                    current_allowance = contract.functions.allowance(
                        self.target_address,
                        item['spender']
                    ).call()
                    
                    if current_allowance > 0:
                        item['allowance'] = current_allowance
                        processed_chain.append(item)
                except Exception as e:
                    print(f"Error processing allowance on chain {chain_id}: {e}")
            
            if processed_chain:
                processed[chain_id] = processed_chain
                
        return processed
        
    def revoke_approval(self, chain_id: int, token_address: str, spender: str) -> Tuple[bool, Optional[str]]:
        """Revoke a single approval following revoke.cash standards"""
        w3 = self.setup_chain_client(chain_id)
        if not w3:
            return (False, None)
            
        chain_data = SUPPORTED_CHAINS[chain_id]
        
        try:
            contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
            
            # Build transaction
            nonce = w3.eth.get_transaction_count(self.target_address)
            gas_price = int(w3.eth.gas_price * GAS_PRICE_MULTIPLIER)
            gas_price = min(gas_price, Web3.to_wei(MAX_GAS_PRICE_GWEI, 'gwei'))
            
            tx = contract.functions.approve(spender, 0).build_transaction({
                'chainId': chain_id,
                'gas': GAS_LIMIT,
                'gasPrice': gas_price,
                'nonce': nonce,
            })
            
            # Estimate gas (adjust if needed)
            try:
                tx['gas'] = contract.functions.approve(spender, 0).estimate_gas({
                    'from': self.target_address,
                    'gasPrice': gas_price
                })
            except:
                pass
                
            # Sign and send
            signed_tx = w3.eth.account.sign_transaction(tx, self.target_account.key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash_str = tx_hash.hex()
            
            # Wait for transaction (with timeout)
            start_time = time.time()
            while time.time() - start_time < 120:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None:
                    return (receipt.status == 1, tx_hash_str)
                time.sleep(5)
                
            print(f"Timeout waiting for transaction {tx_hash_str}")
            return (False, tx_hash_str)
            
        except Exception as e:
            print(f"Error revoking approval on chain {chain_id}: {e}")
            return (False, None)
            
    def revoke_all_allowances(self) -> Dict[str, Any]:
        """Main function to revoke all allowances"""
        print("\nScanning for allowances across all supported chains...")
        raw_allowances = self.get_all_allowances()
        
        if not raw_allowances:
            return {
                'status': 'success',
                'message': 'No approvals found on any chain',
                'details': {}
            }
            
        print("\nProcessing and verifying allowances...")
        processed_allowances = self.process_allowances(raw_allowances)
        
        if not processed_allowances:
            return {
                'status': 'success',
                'message': 'No active approvals found',
                'details': {}
            }
            
        total_approvals = sum(len(v) for v in processed_allowances.values())
        print(f"\nFound {total_approvals} active approvals across {len(processed_allowances)} chains")
        
        # Prepare results structure
        results = {
            'status': 'partial',
            'message': '',
            'details': {},
            'chain_results': {},
            'success_count': 0,
            'failed_count': 0,
            'sponsored_chains': []
        }
        
        # Process each chain
        for chain_id, allowances in processed_allowances.items():
            chain_data = SUPPORTED_CHAINS[chain_id]
            w3 = self.setup_chain_client(chain_id)
            
            if not w3:
                results['details'][chain_id] = {'error': 'Failed to connect to chain'}
                results['failed_count'] += len(allowances)
                continue
                
            print(f"\n=== Processing {len(allowances)} approvals on {chain_data['name']} ===")
            
            # Check gas balance
            balance = w3.eth.get_balance(self.target_address)
            needs_sponsor = balance < Web3.to_wei(0.001, 'ether')  # Very low threshold
            
            if needs_sponsor and self.gas_sponsor:
                print("Insufficient gas, requesting sponsorship...")
                sponsor_tx = self.gas_sponsor.send_gas(self.target_address, chain_id)
                if sponsor_tx:
                    print(f"Received gas sponsorship! TX: {chain_data['tx_url']}{sponsor_tx}")
                    results['sponsored_chains'].append(chain_id)
                    # Wait for sponsorship to be confirmed
                    time.sleep(15)
                else:
                    print("Failed to get sponsorship, skipping chain")
                    results['details'][chain_id] = {'error': 'Failed to get gas sponsorship'}
                    results['failed_count'] += len(allowances)
                    continue
            
            # Process revokes in parallel
            chain_success = 0
            chain_failed = 0
            chain_txs = []
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(
                        self.revoke_approval,
                        chain_id,
                        allowance['token_address'],
                        allowance['spender']
                    ): allowance for allowance in allowances
                }
                
                for future in as_completed(futures):
                    allowance = futures[future]
                    token_symbol = allowance['token_info'].get('symbol', 'UNKNOWN')
                    
                    try:
                        success, tx_hash = future.result()
                        if success:
                            print(f"Revoked {token_symbol} -> {allowance['spender'][:8]}... (TX: {tx_hash})")
                            chain_success += 1
                            chain_txs.append({
                                'token': allowance['token_address'],
                                'spender': allowance['spender'],
                                'tx_hash': tx_hash,
                                'status': 'success'
                            })
                        else:
                            print(f"Failed to revoke {token_symbol} (TX: {tx_hash})")
                            chain_failed += 1
                            chain_txs.append({
                                'token': allowance['token_address'],
                                'spender': allowance['spender'],
                                'tx_hash': tx_hash,
                                'status': 'failed'
                            })
                    except Exception as e:
                        print(f"Error processing approval: {e}")
                        chain_failed += 1
                        chain_txs.append({
                            'token': allowance['token_address'],
                            'spender': allowance['spender'],
                            'error': str(e),
                            'status': 'error'
                        })
            
            # Update results
            results['chain_results'][chain_id] = {
                'success': chain_success,
                'failed': chain_failed,
                'transactions': chain_txs
            }
            results['success_count'] += chain_success
            results['failed_count'] += chain_failed
            
            print(f"Chain result: {chain_success} successful, {chain_failed} failed")
        
        # Final status
        if results['failed_count'] == 0:
            results['status'] = 'success'
            results['message'] = 'All approvals revoked successfully'
        elif results['success_count'] > 0:
            results['message'] = f"Revoked {results['success_count']} approvals ({results['failed_count']} failed)"
        else:
            results['status'] = 'failed'
            results['message'] = 'Failed to revoke any approvals'
            
        return results

def display_results(results: Dict[str, Any]):
    """Display results in a user-friendly format"""
    print("\n=== Revoke Results ===")
    print(f"Status: {results['status'].upper()}")
    print(results['message'])
    
    if results.get('sponsored_chains'):
        print("\nGas was sponsored for chains:")
        for chain_id in results['sponsored_chains']:
            print(f"- {SUPPORTED_CHAINS[chain_id]['name']}")
    
    print("\nChain Details:")
    for chain_id, chain_result in results.get('chain_results', {}).items():
        chain_data = SUPPORTED_CHAINS[chain_id]
        print(f"\n{chain_data['name']}:")
        print(f"  Success: {chain_result['success']}")
        print(f"  Failed: {chain_result['failed']}")
        
        if chain_result['failed'] > 0:
            print("  Failed Transactions:")
            for tx in chain_result['transactions']:
                if tx['status'] != 'success':
                    print(f"    - Token: {tx['token']}")
                    print(f"      Spender: {tx['spender']}")
                    if 'error' in tx:
                        print(f"      Error: {tx['error']}")
                    elif 'tx_hash' in tx:
                        print(f"      TX: {chain_data['tx_url']}{tx['tx_hash']}")

def main():
    print("=== Ultimate Wallet Revoker (Revoke.cash Compatible) ===")
    print("This tool follows revoke.cash standards to securely revoke token approvals\n")
    
    target_key = input("Enter target wallet private key (or leave empty to exit): ").strip()
    if not target_key:
        print("Exiting...")
        return
        
    use_sponsor = input("Do you want to use gas sponsorship? (yes/no): ").strip().lower() == 'yes'
    sponsor_key = ""
    
    if use_sponsor:
        sponsor_key = input("Enter sponsor wallet private key (for gas fees): ").strip()
        if not sponsor_key:
            print("No sponsor key provided, continuing without sponsorship")
            
    print("\nInitializing revoker...")
    revoker = WalletRevoker(target_key, sponsor_key if use_sponsor else None)
    
    print(f"\nTarget wallet address: {revoker.target_address}")
    if use_sponsor and revoker.gas_sponsor:
        print(f"Gas sponsor address: {revoker.gas_sponsor.sponsor_address}")
    
    confirm = input("\nAre you sure you want to revoke ALL token approvals across all supported chains? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Operation cancelled")
        return
        
    print("\nStarting comprehensive revoke process...")
    start_time = time.time()
    
    results = revoker.revoke_all_allowances()
    
    elapsed = time.time() - start_time
    results['execution_time'] = elapsed
    
    display_results(results)
    print(f"\nTotal execution time: {elapsed:.2f} seconds")

if __name__ == "__main__":
    main()