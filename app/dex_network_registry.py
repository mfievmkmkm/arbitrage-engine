"""Paper network identities. USDC addresses: Circle mainnet registry.
Sources: https://developers.circle.com/stablecoins/usdc-contract-addresses
https://docs.0x.org/docs/introduction/supported-chains
"""
import json
import os

CHAINS = {'SOLANA':'solana','ETHEREUM':1,'ETH':1,'BASE':8453,'ARBITRUM':42161,
 'OPTIMISM':10,'OP':10,'POLYGON':137,'AVALANCHE':43114,'AVAX':43114,
 'BSC':56,'BNB':56,'ABSTRACT':2741,'ARC':5042,'BERACHAIN':80094,
 'HYPEREVM':999,'INK':57073,'LINEA':59144,'MANTLE':5000,'MONAD':143,
 'PLASMA':9745,'ROBINHOOD':4663,'SCROLL':534352,'SONIC':146,'TEMPO':4217,
 'UNICHAIN':130,'WORLDCHAIN':480,'WORLD':480}
# Gecko network id, exact trusted stable contract.
STABLES = {
 56:('bsc','0x55d398326f99059fF775485246999027B3197955'),
 1:('eth','0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'),
 42161:('arbitrum','0xaf88d065e77c8cC2239327C5EDb3A432268e5831'),
 5042:('arc','0x3600000000000000000000000000000000000000'),
 43114:('avax','0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E'),
 8453:('base','0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'),
 999:('hyperevm','0xb88339CB7199b77E23DB6E890353E22632Ba630f'),
 57073:('ink','0x2D270e6886d130D724215A266106e6832161EAEd'),
 59144:('linea','0x176211869cA2b568f2A7D4EE941E073a821EE1ff'),
 143:('monad','0x754704Bc059F8C67012fEd69BC8A327a5aafb603'),
 10:('optimism','0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85'),
 9745:('plasma','0x2d661C89D812261039AF9764eceaAee884f5F67F'),
 137:('polygon_pos','0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359'),
 146:('sonic','0x29219dd400f2Bf60E5a23d13Be72B486D4038894'),
 130:('unichain','0x078D782b760474a361dDA0AF3839290b0EF57AD6'),
 480:('world-chain','0x79A02482A880bCe3F13E09da970dC34dB4cD24D1'),
}


def stable_config(chain):
    from .zerox_research import ADDRESS
    configured = json.loads(os.getenv('DEX_EVM_NETWORKS_JSON','{}'))
    override = configured.get(str(chain))
    if override:
        network, stable = override['network'], override['stable']
    elif chain in STABLES:
        network, stable = STABLES[chain]
    else:
        raise ValueError('Сеть распознана, но нет проверенного базового стейблкоина: chainId=' + str(chain))
    if not isinstance(network,str) or not network or not all(c.isalnum() or c in '-_' for c in network) or not ADDRESS.fullmatch(stable):
        raise ValueError('DEX_EVM_NETWORK_CONFIG_INVALID')
    return network, stable
