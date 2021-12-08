from collections import defaultdict
from io import BytesIO
from OpusDecoder import MyDecoder as Decoder
from itertools import zip_longest
import numpy as np
import wave

class PacketQueue:
    def __init__(self):
        self.queues = defaultdict(list)
    
    def push(self,packet):
        self.queues[packet.ssrc].append(packet)
    
#----------- ssrc packet -----------
    def get_all_ssrc(self):
        return self.queues.keys()
    
    def get_packets(self, ssrc:int):
        last_seq=None
        packets = self.queues[ssrc]

        while packets:
            if last_seq is None:
                packet = packets.pop(0)
                last_seq = packet.seq
                yield packet
                continue
            if last_seq == 65535:
                last_seq = -1
            if packets[0].seq - 1 == last_seq:
                packet = packets.pop(0)
                last_seq = packet.seq
                yield packet
                continue
            
            for i in range(1, min(1000, len(packets))):
                if packets[i].seq - 1 == last_seq:
                    packet = packets.pop(0)
                    last_seq = packet.seq
                    yield packet
                    break
            else:
                yield None

            yield -1
class BufferDecoder:
    def __init__(self):
        self.queue = PacketQueue()
    
    def recv_packet(self,packet):
        self.queue.push(packet)
    
    async def _decode(self,ssrc):
        decoder = Decoder()
        pcm = []
        start_time = None
        last_timestamp = None

        for packet in self.queue.get_packets(ssrc):
            if packet == -1:
                # 終了
                break
            if packet is None:
                # パケット破損の場合
                data = decoder.decode_float(None)
                pcm += data
                last_timestamp = None
                continue

            if start_time is None:
                start_time = packet.real_time
            else:
                start_time = min(packet.real_time, start_time)

            if len(packet.decrypted) < 10:
                # パケットがdiscordから送られてくる無音のデータだった場合: https://discord.com/developers/docs/topics/voice-connections#voice-data-interpolation
                last_timestamp = packet.timestamp
                continue

            if last_timestamp is not None:
                elapsed = (packet.timestamp - last_timestamp) / Decoder.SAMPLING_RATE
                if elapsed > 0.02:
                    # 無音期間
                    margin = [0] * 2 * int(Decoder.SAMPLE_SIZE * (elapsed - 0.02) * Decoder.SAMPLING_RATE)
                    pcm += margin

            data = decoder.decode_float(packet.decrypted)
            pcm += data
            last_timestamp = packet.timestamp

        del decoder

        print(dict)
        
        return dict(data=pcm, start_time=start_time)

    async def decode(self):
        file = BytesIO()
        wav = wave.open(file, "wb")
        wav.setnchannels(Decoder.CHANNELS)
        wav.setsampwidth(Decoder.SAMPLE_SIZE // Decoder.CHANNELS)
        wav.setframerate(Decoder.SAMPLING_RATE)
        pcm_list = []

        print("デコードを開始します")

        for ssrc in self.queue.get_all_ssrc():
            pcm_list.append(await self._decode(ssrc))

        pcm_list.sort(key=lambda x: x["start_time"])

        if not pcm_list:
            # 音声がなかった場合
            wav.close()
            file.seek(0)
            return file
        # 録音が始まった時刻
        first_time = pcm_list[0]["start_time"]
        for pcm in pcm_list:
            # 録音が始まった時刻からのマージンをつける
            pcm["data"] = [0] * int(48000 * 2 * (pcm["start_time"] - first_time)) + pcm["data"]

        right_channel = []
        left_channel = []

        i = 0
        for bytes_ in zip_longest(*map(lambda x: x["data"], pcm_list)):
            # 左右のチャンネルにそれぞれ音声を合成してから入れる処理
            result = 0
            for b in bytes_:
                if b is None:
                    continue
                # 音声の合成
                # result = x + y - (x * y * -1) when x < 0 and y < 0
                # result = x + y - (x * y) when x > 0 and y > 0
                # otherwise, result = x + y
                if result < 0 and b < 0:
                    result = result + b - (result * b * -1)
                elif result > 0 and b > 0:
                    result = result + b - (result * b)
                else:
                    result = result + b

            # クリッピングの対処
            if result > 1:
                if not i % 2:
                    right_channel.append(1)
                else:
                    left_channel.append(1)
            elif result < -1:
                if not i % 2:
                    right_channel.append(-1)
                else:
                    left_channel.append(-1)
            else:
                if not i % 2:
                    right_channel.append(result)
                else:
                    left_channel.append(result)
            i += 1

        # 左右のチャンネルの大きさが違う場合があるので、その場合の処理
        left_len = len(left_channel)
        right_len = len(right_channel)
        if left_len != right_len:
            if not left_len % 2:
                if left_len > right_len:
                    right_channel += [0] * (left_len - right_len)
                else:
                    right_channel = right_channel[:left_len]
            elif not right_len % 2:
                if right_len > left_len:
                    left_channel += [0] * (right_len - left_len)
                else:
                    left_channel = left_channel[:right_len]

        audio = np.array([left_channel, right_channel]).T

        # Convert to (little-endian) 16 bit integers.
        audio = (audio * (2 ** 15 - 1)).astype(np.int16)

        wav.writeframes(audio.tobytes())
        wav.close()
        
        w = wave.Wave_write("test.wav")
        w.setnchannels(Decoder.CHANNELS)
        w.setsampwidth(Decoder.SAMPLE_SIZE // Decoder.CHANNELS)
        w.setframerate(Decoder.SAMPLING_RATE)
        w.writeframes(audio.tobytes())
        w.close
        file.seek(0)

        print(file.getvalue())
        print(type(file))
        print("デコードを終了します")
        return file