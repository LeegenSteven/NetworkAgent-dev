from base64 import b64encode

from nacl.public import PrivateKey as _PrivateKey

class WgKey:
    """Wireguard key pair"""

    def __init__(self):
        self._key = _PrivateKey.generate()
        self._name = None

    def __str__(self) -> str:
        return self.pubkey

    @property
    def pubkey(self) -> str:
        """The base 64 encoded public key"""
        return b64encode(bytes(self._key.public_key)).decode("ascii")

    @property
    def privkey(self) -> str:
        """The base 64 encoded private key"""
        return b64encode(bytes(self._key)).decode("ascii")

    @property
    def name(self):
        """The name of the key

        Based on the string searched in the public key.
        """
        return self._name

    @name.setter
    def name(self, value) -> None:
        self._name = value

    def to_dict(self):
        return {
            'private': self.privkey,
            'public': self.pubkey
        }
