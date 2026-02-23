# roscamp-repo-2
ROS2와 AI를 활용한 자율주행 로봇개발자 부트캠프 2팀 저장소. ROS2 기반 자율 시스템 무인 택시 충전·운영 통합 플랫폼 (AutoServe)
<img width="1080" height="1080" alt="로고" src="https://github.com/user-attachments/assets/92fddc97-94ce-4efc-8d83-4f9b04b3f1e3" />
<h1 align="center">🚖 TASHO</h1>

<p align="center">2-PRO 팀이 개발한, AI 기반 자율 시스템 무인 택시 충전·운영 통합 시스템입니다.</p>


# 📌 프로젝트 개요 (Project Scenario)
본 프로젝트는 ROS 2를 기반으로 자율주행 무인 택시 시스템을 설계하고 구현하는 것을 목표로 한다. 본 시스템은 자율주행 이동 기능을 중심으로, 배터리 상태 인식과 자동 충전 기능을 통합하여 사람의 개입 없이 지속적으로 운용 가능한 무인 이동 플랫폼을 구현한다.
본 프로젝트에서는 자율주행 로봇, 로봇팔을 활용한 충전 시스템, 사용자 목적지 선택 인터페이스를 하나의 통합 구조로 구성하며, 이를 통해 실제 무인 택시 운영 환경을 가정한 시스템 구현 가능성을 검증하고자 한다.

- **프로젝트명**: TASHO
- **팀명**: 2-PRO
- **주제**: AI 기반 자율 시스템 무인 택시 충전·운영 통합 시스템
- **핵심 기술**: ROS2, YOLO, OpenCV, UDP, TCP, HTTP

# 👥 팀 구성 및 역할 (Team Roles)
|        | NAME | JOB |
|:------:|:----:|:----:|
| Leader  | 조건희 | 여기 작성 |
| Worker   | 김다준 | 여기 작성 |
| Worker   | 박건우 | 핑키 차선,사람,핑키차량 욜로 학습 및 인식 정확도 향상을 위한 로직을 개발,사용자 GUI 초기 툴 개발 |
| Worker   | 정현준 | 여기 작성 |
| Worker   | 최원준 | 여기 작성 |
| Worker   | 함주현 | 여기 작성 |

# ⚙️ 기술 스택 (Tech Stack)
- **Hardware Platform**:Raspberry Pi(PinkyBot, Jetcobot)
- **Development Environment**: Ubuntu(24.04) ROS2(Jazzy), OpenCV, YOLOv8, PyQt
- **Programming Languages**:Python, MySQL
- **Sensors & Perception Devices**LiDAR, Camera, IR Sensor
- **Communication Protocols**: TCP, UDP, HTTP
- **TOS2 Middleware & Messaging**: ROS2 Domain Bridge
- **Configuration Management**: Github, Jira, Confluence, Slack

# 📁 시스템 구성 (System Architecture)
## Hardware Architecture
<img width="1446" height="1151" alt="HW Architecture drawio" src="https://github.com/user-attachments/assets/53291e35-87c6-42cc-9eae-699798a74f67" />

## Software Architecture
<img width="1579" height="1039" alt="Software Architecture drawio (1)" src="https://github.com/user-attachments/assets/b4b87e09-612a-45dc-ab7c-a0fc8aa9a1fd" />

## System Architecture
<img width="1571" height="1053" alt="Software Architecture drawio" src="https://github.com/user-attachments/assets/f3425962-177e-414b-9d93-51427299ffd9" />

## Sequence Diagram
<img width="735" height="960" alt="무인택시운행 drawio" src="https://github.com/user-attachments/assets/579fe168-b0d9-44d6-97bb-ceb087fd59a7" />
<img width="941" height="1109" alt="Sequence Diagram-Charge Motion drawio" src="https://github.com/user-attachments/assets/3e3c8390-af26-4ebc-a2c0-52b4f7e6fdc1" />

## Map
<img width="998" height="498" alt="캡" src="https://github.com/user-attachments/assets/aa64f28b-5bc2-4090-a59e-82adfe0bde2f" />



# 🛠 Implementation
## Scenario
### 시나리오 1 - 무인 택시 호출
![시나리오 1 호출](https://github.com/user-attachments/assets/3883a51b-1379-436a-a064-79145245c792)
<img src="images/gui.png" alt="GUI" width="800" />
<img src="images/wngod.png" alt="GUI" width="800" />

### 시나리오 2 - 무인 택시 주행
![시나리오 2 주행](https://github.com/user-attachments/assets/e0103423-531c-4a1d-9bd8-88ff34d97043)


### 시나리오 3 - 로봇팔 충전 
**조건:** 배터리 ≤ 30%
![시나리오 3 로봇팔 충전](https://github.com/user-attachments/assets/0390a252-9746-4ba2-ac99-fc3be3f6aa96)


# 🎥 Demonstration Video
## 영상 제목
(영상)


# 📅 Project Schedule
Project Period: 2025.12.29~2026.02.27

(Jira 스케쥴)
