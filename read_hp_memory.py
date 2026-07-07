import ctypes
import sys

# Define Windows API constants and structures
PROCESS_VM_READ = 0x0010

# Function to read a 4-byte integer from a process's memory
def read_memory_int(pid, address):
    try:
        # Open the process with read access
        # False means we don't inherit handles
        # pid is the process ID
        process_handle = ctypes.windll.kernel32.OpenProcess(PROCESS_VM_READ, False, pid)
        
        if not process_handle:
            print(f"Error: Could not open process with PID {pid}. Check permissions (run as administrator).")
            return None

        # Create a buffer to store the read value (4 bytes for an integer)
        buffer = ctypes.c_uint()
        bytes_read = ctypes.c_size_t()

        # Read the memory
        # process_handle: handle to the process
        # address: base address to read from
        # ctypes.byref(buffer): pointer to the buffer to store data
        # ctypes.sizeof(buffer): number of bytes to read
        # ctypes.byref(bytes_read): pointer to a variable that receives the number of bytes read
        if ctypes.windll.kernel32.ReadProcessMemory(process_handle, address, ctypes.byref(buffer), ctypes.sizeof(buffer), ctypes.byref(bytes_read)):
            return buffer.value
        else:
            print(f"Error: Could not read memory at address {hex(address)}. Error code: {ctypes.windll.kernel32.GetLastError()}")
            return None

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None
    finally:
        # Close the process handle
        if process_handle:
            ctypes.windll.kernel32.CloseHandle(process_handle)

# Example Usage (replace with actual PID and address)
if __name__ == "__main__":
    # IMPORTANT: Replace these with the actual PID of your game and the memory address
    # You would typically get the PID using a tool like Task Manager or by finding the window.
    # The memory address would be found using a memory scanner like Cheat Engine.
    
    # Example: Assume game PID is 12345 and HP is at address 0x7FF7B4A00000
    # These are placeholder values and will NOT work for your specific game.
    
    # To test, you can try reading from a known process like Notepad.exe
    # Find Notepad's PID from Task Manager.
    # Find a simple value in Notepad's memory (e.g., a character count, though this is complex).
    # For a real game, you'd use Cheat Engine to find the HP address.

    # Placeholder values - YOU MUST CHANGE THESE
    target_pid = 3408  # Example PID
    target_address = 0x7FF7B4A00000 # Example address (replace with your actual address)

    print(f"Attempting to read memory from PID: {target_pid} at address: {hex(target_address)}")
    hp_value = read_memory_int(target_pid, target_address)

    if hp_value is not None:
        print(f"Successfully read HP value: {hp_value}")
    else:
        print("Failed to read HP value.")

    print("\n--- Instructions ---")
    print("1. Find the Process ID (PID) of your game using Task Manager.")
    print("2. Use a memory scanning tool (like Cheat Engine) to find the exact memory address of your HP.")
    print("3. Replace 'target_pid' and 'target_address' in this script with your findings.")
    print("4. Run this script as administrator if you encounter 'Could not open process' errors.")
    print("5. If HP is not a 4-byte integer, you will need to adjust 'ctypes.c_uint()' and potentially the interpretation.")
