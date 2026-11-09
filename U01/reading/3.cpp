//
// Created by sukuna on 11/6/26.
//
import std;

void printSquare(int value) {
    std::println("The square of {} is {}", value, value * value);
}

int main() {
    printSquare(4);
    printSquare(9);
    return 0;
}
