require 'socket'
require 'time'

# Function to send RFC 3164 syslog message via UDP
def send_syslog(message, host: '127.0.0.1', port: 5140, facility: 1, severity: 6, tag: 'ruby_logger', hostname: 'localhost')
  # Calculate priority (Facility * 8 + Severity)
  # Default Facility: 1 (User-level messages)
  # Default Severity: 6 (Informational)
  pri = (facility * 8) + severity

  # Format timestamp (RFC 3164: Mmm dd hh:mm:ss)
  # Note: %e provides a space-padded day of the month (1-31)
  timestamp = Time.now.strftime('%b %e %H:%M:%S')

  # Construct packet: <PRI>TIMESTAMP HOSTNAME TAG: CONTENT
  # Example: <14>Feb 06 14:31:27 localhost ruby_logger: This is a test message
  packet = "<#{pri}>#{timestamp} #{hostname} #{tag}: #{message}"

  # Send via UDP
  socket = UDPSocket.new
  begin
    socket.send(packet, 0, host, port)
    puts "Successfully sent: #{packet}"
  rescue => e
    puts "Error sending syslog message: #{e.message}"
  ensure
    socket.close
  end
end

# Example usage if run directly
if __FILE__ == $0
  base_message = ARGV[0] || "Test message from Ruby script"
  target_host = ARGV[1] || '127.0.0.1'
  target_port = (ARGV[2] || 5140).to_i

  puts "Sending 5 messages to #{target_host}:#{target_port}..."
  
  5.times do |i|
    # Sleep a tiny bit to ensure timestamps might vary if the system clock supports it, 
    # though RFC3164 only has second precision so they'll likely be the same timestamp.
    # But it's good practice not to flood instantly if testing rate limits.
    sleep 0.1 
    
    sequence_num = i + 1
    msg_with_seq = "[Seq #{sequence_num}/5] #{base_message}"
    send_syslog(msg_with_seq, host: target_host, port: target_port)
  end
end
