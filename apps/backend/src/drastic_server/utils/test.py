from hashlib import sha256

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad


def encrypt_string(input_string, key):
    # Erzeuge einen AES-Schlüssel mit 32 Bytes Länge
    key = sha256(key.encode()).digest()
    
    # Initialisierungsvektor (IV) erzeugen
    iv = get_random_bytes(16)
    
    # AES-Cipher im CBC-Modus initialisieren
    cipher = AES.new(key, AES.MODE_CBC, iv)
    
    # Eingabestring paddieren und verschlüsseln
    ciphertext = cipher.encrypt(pad(input_string.encode(), AES.block_size))
    
    return iv + ciphertext

def decrypt_string(ciphertext, key):
    # Erzeuge den AES-Schlüssel mit 32 Bytes Länge
    key = sha256(key.encode()).digest()
    
    # IV extrahieren
    iv = ciphertext[:16]
    
    # Verschlüsselten Text extrahieren
    ciphertext = ciphertext[16:]
    
    # AES-Cipher im CBC-Modus initialisieren
    cipher = AES.new(key, AES.MODE_CBC, iv)
    
    # Entschlüsseln und entpaddieren
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
    
    return plaintext.decode()

# Beispielnutzung
key = "geheimespasswort"
plaintext = "Hallo, das ist ein geheimer Text!"

# Verschlüsseln
ciphertext = encrypt_string(plaintext, key)
print("Verschlüsselter Text:", ciphertext)

# Entschlüsseln
decrypted_text = decrypt_string(ciphertext, key)
print("Entschlüsselter Text:", decrypted_text)
