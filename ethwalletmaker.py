from secrets import token_bytes
from coincurve import PublicKey
from Crypto.Hash import keccak

def keccak_256(data):
    return keccak.new(digest_bits=256, data=data)
print("Vanity Etherium Wallet Maker.\nby: thelamewizard")
finalResult =""
pairs= {"e":2}
def make():
    global finalResult
    global addr
    global private_key
    global public_key
    private_key = keccak_256(token_bytes(32)).digest()
    public_key = PublicKey.from_valid_secret(private_key).format(compressed=False)[1:]
    addr = keccak_256(public_key).digest()[-20:]
    finalResult = addr.hex()
    return finalResult

def getit():
    
    make()
    print('Private Key:', private_key.hex())
    print('Public ETH Address: 0x' + finalResult)
   

def runit(x=input("Only characters 0-9 and lower-case a-f are valid.\nEnter your chosen leading characters:\n").lower()):
    global finalResult
    itter = 0
    while finalResult[0:len(x)] != x:
        getit()
        print(finalResult[0:len(x)])
        itter+=1
    print("\n\n"+str(itter) +" wallets itterated through to find your chosen one")
    print('Private Key:', private_key.hex())
    print('Public ETH Address: 0x' + finalResult)
    
        
if __name__ == "__main__":
    runit()