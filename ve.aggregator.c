// VE.Direct Aggregator — Arduino Mega 2560
// Serial1 RX → charger 1
// Serial2 RX → charger 2
// Serial3 RX → charger 3
// Serial0 TX → RS485 output (to RPi)
// Baud: 19200 on all ports
//
// A marker line is sent before the first block:
//   ---\tN\r\n
// N = number of chargers in this packet
// A downstream aggregator can use this to pass blocks through transparently.

#define BAUD      19200
#define BUF_SIZE  512

HardwareSerial* ports[] = {&Serial1, &Serial2, &Serial3};
const int N = sizeof(ports) / sizeof(ports[0]);

char  buf[3][BUF_SIZE];
int   buf_len[3] = {0, 0, 0};
bool  ready[3]   = {false, false, false};
char  prev[3]    = {0, 0, 0};

void setup() {
	Serial.begin(BAUD);
	for (int i = 0; i < N; i++) {
		ports[i]->begin(BAUD);
	}
}

void read_charger(int idx) {
	HardwareSerial* port = ports[idx];

	while (port->available() && !ready[idx]) {
		char c = port->read();

		// two consecutive \n signals end of VE.Direct block
		if (c == '\n' && prev[idx] == '\n') {
			if (buf_len[idx] > 0) {
				// write final \n into buffer before marking as ready
				if (buf_len[idx] < BUF_SIZE - 1) {
					buf[idx][buf_len[idx]++] = c;
				}
				ready[idx] = true;
			}
			return;
		}

		prev[idx] = c;

		if (buf_len[idx] < BUF_SIZE - 1) {
			buf[idx][buf_len[idx]++] = c;
		}
	}
}

void send_blocks() {
	// count ready blocks
	int count = 0;
	for (int i = 0; i < N; i++) {
		if (ready[i]) count++;
	}
	if (count == 0) return;

	// send marker: ---\tN\r\n
	Serial.print("---\t");
	Serial.print(count);
	Serial.print("\r\n");

	// send blocks sequentially
	for (int i = 0; i < N; i++) {
		if (ready[i]) {
			Serial.write(buf[i], buf_len[i]);
			Serial.flush();
			buf_len[i] = 0;
			ready[i]   = false;
			prev[i]    = 0;
		}
	}
}

void loop() {
	for (int i = 0; i < N; i++) {
		read_charger(i);
	}
	send_blocks();
}
